"""CEDAR release presentation."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
from rich import box
from rich.table import Table
from rich.text import Text
import json
import time
import typer
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.lifecycle import (
    RELEASE_STAGES,
    RELEASE_TERMINAL_PHASE,
    _next_release_stage,
    _release_stage_has_finished,
)
from org.metadatacenter.release_support.output import (
    console,
)
from org.metadatacenter.release_support.preflight import (
    PreflightFinding,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.validation import (
    ReleaseBuildValidator,
)


def _render_plan(manifest: dict) -> None:
    cee = manifest["cee"]
    console.print(f"Release:             {manifest['releaseVersion']}")
    console.print(f"Next development:    {manifest['nextDevelopmentVersion']}")
    console.print(f"Source train:        {manifest['train']}")
    console.print(
        "CEE equivalence:     "
        f"{cee['development']['version']} -> {cee['public']['version']}"
    )
    console.print(f"CEE payload SHA-256: {cee['promotionProof']['normalizedPayloadSha256']}")
    console.print("CEE executable:      identical after declared release-provenance changes")


def _publication_progress(manifest: dict) -> tuple[int, int]:
    records = {}
    for field in ("artifactPublication", "snapshotPublication"):
        section = manifest.get(field) or {}
        value = section.get("completedTasks", {}) if isinstance(section, dict) else {}
        if isinstance(value, dict):
            records.update(value)
    snapshot = sum(identifier.startswith("maven:nextDevelopment:") for identifier in records)
    return len(records) - snapshot, snapshot


def _release_progress(manifest: dict) -> list[dict]:
    """Build the compact phase model used by both human and JSON status."""
    state = ReleaseState()
    completed_release, completed_snapshots = _publication_progress(manifest)
    completed = {
        "frontends": int(bool(manifest.get("frontendPreparation"))),
        "versions": int(bool(manifest.get("versionPreparation"))),
        "builds": len(manifest.get("buildValidation", {}).get("completedTasks", {})),
        "local-refs": len(manifest.get("localRefs", {}).get("completedTasks", {})),
        "snapshots": completed_snapshots,
        "remotes": len(manifest.get("remoteIntegration", {}).get("completedTasks", {})),
        "artifacts": completed_release,
        "acceptance": int(manifest.get("phase") == RELEASE_TERMINAL_PHASE),
    }
    release_repositories = list(manifest.get("releaseRepositories", []))
    release_ref_repositories = set(release_repositories)
    for consumer in manifest.get("cee", {}).get("consumers", []):
        repository = consumer.get("repository")
        if repository:
            release_ref_repositories.add(repository)
    plan = manifest.get("publicationPlan", {})
    totals = {
        "frontends": 1,
        "versions": 1,
        "builds": completed["builds"],
        "local-refs": len(release_ref_repositories) + len(release_repositories),
        "snapshots": len(manifest.get("mavenPhases", [])) + 1,
        "remotes": len(release_ref_repositories),
        "artifacts": 2 + len(plan.get("npm", {}).get("surfaces", [])),
        "acceptance": 1,
    }
    if manifest.get("frontendPreparation"):
        try:
            totals["builds"] = len(ReleaseBuildValidator(state).tasks(manifest))
        except ReleaseError:
            # Before every isolated workspace is inspectable, preserve an honest lower
            # bound instead of making status fail.
            pass
    next_stage = _next_release_stage(manifest)
    publication = manifest.get("artifactPublication") or {}
    file_progress = (
        publication.get("inProgressTask") if isinstance(publication, dict) else None
    )
    rows = []
    for stage in RELEASE_STAGES:
        if _release_stage_has_finished(manifest, stage.name):
            phase_state = "complete"
        elif stage.name == next_stage:
            phase_state = "failed" if manifest.get("failure") else "next"
        else:
            phase_state = "pending"
        row = {
            "phase": stage.name,
            "state": phase_state,
            "completed": completed[stage.name],
            "total": max(totals[stage.name], completed[stage.name]),
        }
        if (
            stage.name == "artifacts"
            and isinstance(file_progress, dict)
            and file_progress.get("kind") == "maven-release-upload"
        ):
            row["detail"] = (
                f"Maven files {file_progress.get('completedFiles', 0)}/"
                f"{file_progress.get('totalFiles', '?')}"
            )
        rows.append(row)
    return rows


def _render_release_status(manifest: dict, path: Path) -> None:
    if manifest.get("phase") == "abandoned":
        abandonment = manifest.get("abandonment", {})
        heading = Text(
            f"Release {manifest.get('releaseVersion')} — ABANDONED", style="yellow",
        )
        console.print(heading)
        console.print(f"Ledger: {manifest.get('phase')}")
        console.print(f"Previous phase: {abandonment.get('previousPhase', 'unknown')}")
        console.print(f"Reason: {abandonment.get('reason', 'not recorded')}")
        if manifest.get("failure"):
            console.print(f"[red]Last failure: {manifest['failure']}[/red]")
        console.print(f"State: {path}")
        return
    complete = manifest.get("phase") == RELEASE_TERMINAL_PHASE
    heading = Text(f"Release {manifest.get('releaseVersion')} — ")
    heading.append(
        "COMPLETE" if complete else "INCOMPLETE",
        style="green" if complete else "yellow",
    )
    console.print(heading)
    console.print(f"Ledger: {manifest.get('phase')}")
    if manifest.get("lastAttempt"):
        console.print(f"Attempt: {manifest['lastAttempt']}")
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, pad_edge=False)
    table.add_column("Phase")
    table.add_column("State")
    table.add_column("Progress", justify="right")
    for row in _release_progress(manifest):
        style = {"complete": "green", "failed": "red", "next": "yellow"}.get(
            row["state"], "dim")
        progress = f"{row['completed']}/{row['total']}"
        if row.get("detail"):
            progress += f" · {row['detail']}"
        table.add_row(
            row["phase"],
            Text(row["state"], style=style),
            progress,
        )
    console.print(table)
    publication = manifest.get("artifactPublication") or {}
    file_progress = (
        publication.get("inProgressTask") if isinstance(publication, dict) else None
    )
    if isinstance(file_progress, dict) and file_progress.get("currentFile"):
        console.print(
            "Current Maven file: "
            f"{file_progress['currentFile']} "
            f"(uploaded {file_progress.get('uploadedFiles', 0)}, "
            f"already present {file_progress.get('existingFiles', 0)})",
            markup=False,
        )
    if manifest.get("failure"):
        console.print(f"[red]Failure: {manifest['failure']}[/red]")
    next_stage = _next_release_stage(manifest)
    if next_stage:
        console.print(f"Next: {next_stage}")
        console.print("Run:  cedarcli release resume")
    console.print(f"State: {path}")


_ACTIVE_RELEASE_SECTIONS = {
    "validating-builds": "buildValidation",
    "creating-local-refs": "localRefs",
    "publishing-snapshots": "snapshotPublication",
    "integrating-remotes": "remoteIntegration",
    "publishing-artifacts": "artifactPublication",
}


def _release_watch_summary(manifest: dict, elapsed: float) -> str:
    phase = manifest.get("phase", "unknown")
    rows = _release_progress(manifest)
    current = next((row for row in rows if row["state"] in {"next", "failed"}), None)
    progress = (
        f"{current['phase']} {current['completed']}/{current['total']}"
        if current else "complete"
    )
    section_name = _ACTIVE_RELEASE_SECTIONS.get(phase)
    section = manifest.get(section_name, {}) if section_name else {}
    active = section.get("inProgressTask") if isinstance(section, dict) else None
    if isinstance(active, dict):
        identifier = active.get("id")
        if active.get("kind") == "maven-release-upload":
            identifier = (
                f"{identifier} files {active.get('completedFiles', 0)}/"
                f"{active.get('totalFiles', '?')}"
            )
    else:
        identifier = active
    detail = f" | active {identifier}" if identifier else ""
    retry = manifest.get("retry")
    if isinstance(retry, dict):
        failure = (
            f" | retry {retry.get('attempt')}/{retry.get('maximum')} in "
            f"{retry.get('delaySeconds')}s: {retry.get('reason')}"
        )
    else:
        failure = f" | failure {manifest['failure']}" if manifest.get("failure") else ""
    minutes, seconds = divmod(max(0, int(elapsed)), 60)
    hours, minutes = divmod(minutes, 60)
    elapsed_text = f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"Release {manifest.get('releaseVersion')} | {progress}{detail}{failure} | {elapsed_text}"


def _watch_release(
    state: ReleaseState, *, sleeper=time.sleep, interval: float = 10,
    heartbeat: float = 60,
) -> tuple[dict, Path, int]:
    started = time.monotonic()
    last_report = started - heartbeat
    previous = None
    while True:
        manifest, path = state.read_current_manifest()
        now = time.monotonic()
        summary = _release_watch_summary(manifest, now - started)
        signature = (
            manifest.get("phase"),
            manifest.get("failure"),
            json.dumps(manifest.get("retry"), sort_keys=True),
            tuple(
                (row["phase"], row["state"], row["completed"], row["total"], row.get("detail"))
                for row in _release_progress(manifest)
            ),
            json.dumps(
                (manifest.get(_ACTIVE_RELEASE_SECTIONS.get(manifest.get("phase"), ""), {}) or {})
                .get("inProgressTask"),
                sort_keys=True,
            ),
        )
        if signature != previous or now - last_report >= heartbeat:
            console.print(summary, markup=False, soft_wrap=True)
            previous = signature
            last_report = now
        phase = manifest.get("phase")
        if phase == RELEASE_TERMINAL_PHASE:
            return manifest, path, 0
        if phase == "abandoned" or (manifest.get("failure") and not manifest.get("retry")):
            return manifest, path, 1
        sleeper(interval)


def _render_preflight_findings(findings: list[PreflightFinding]) -> None:
    failures = [finding for finding in findings if finding.fatal]
    warnings = [finding for finding in findings if not finding.fatal]
    if not findings:
        console.print("Release checks:      every precondition settled")
    else:
        console.print(
            f"Release checks:      {len(failures)} blocking, {len(warnings)} advisory")
    for finding in warnings:
        console.print(f"  [yellow]{finding.check}: {finding.message}[/yellow]")
    for finding in failures:
        console.print(f"  [red]{finding.check}: {finding.message}[/red]")
        if finding.remedy:
            console.print(f"    {finding.remedy}")
    if failures:
        console.print(
            f"[red]{len(failures)} precondition(s) block this release. Nothing was changed."
            "[/red]")
        raise typer.Exit(1)

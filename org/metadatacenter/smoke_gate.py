"""The whole-stack smoke run as evidence a train dispatch and a release plan are gated on.

The estate's rule is that suites verify logic and a redeploy plus a smoke run verifies reality.
Until this module nothing enforced the second half: the REST tier and the browser tier under
`cedar-development/ops/e2e` ran when a developer remembered, and a contract regression that every
backend-free suite passes could reach staging through a train. `cedarcli test e2e` runs both tiers
against the native stack and records what they ran against. The train dispatch preflight and the
release plan refuse a source that no passing run covers.

A run is evidence about commits, not about a moment. The record therefore names the `develop` head
of every train repository at the time of the run, and the gate compares those heads with the source
being trained or released. A run is kept under a digest of the heads it tested rather than as one
latest file, because a train is released hours or days after it is dispatched and `develop` moves
on in between: a rerun after a documentation commit must not erase the evidence the release still
needs.

The record must also answer for the jars. The controller's own `current` column establishes that
no process predates its jar, which is a narrower question than it looks: a jar can itself predate
the commit the record names, and every row reads `current` while it does. A run therefore refuses
to start while any service is stale or unhealthy, and equally while any deployed jar was written
before its repository's develop head, because the evidence would otherwise certify code the stack
is not running.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Mapping

from rich.console import Console

from org.metadatacenter.worker.ServerWorker import ServerWorker

console = Console()

SCHEMA_VERSION = 1
TIERS = ("rest", "browser")
REMEDY = "run cedarcli test e2e against the stack built from this source"

E2E = Path("cedar-development") / "ops" / "e2e"
REPORTS = E2E / "reports" / "smoke-gate"
BUILD_TRAIN = Path("cedar-development") / "ops" / "build-train.json"
CONTROLLER = Path("cedar-development") / "ops" / "cedar-services.sh"

HEALTHY = {"healthy", "docker"}
SUMMARY_LIMIT = 5


class SmokeGateError(ValueError):
    """The gate could not be answered from what is recorded."""


def _home(cedar_home) -> Path:
    if not cedar_home:
        raise SmokeGateError("CEDAR_HOME is not set")
    return Path(cedar_home)


def reports_dir(cedar_home) -> Path:
    return _home(cedar_home) / REPORTS


def train_repositories(cedar_home) -> list[str]:
    """The repositories a train captures, which are the ones a run must speak for."""
    path = _home(cedar_home) / BUILD_TRAIN
    try:
        configuration = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeGateError(f"cannot read build-train configuration: {error}") from error
    repositories = configuration.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise SmokeGateError(f"{path} names no repositories")
    return list(repositories)


def sources_digest(sources: Mapping[str, str]) -> str:
    """One name for one set of heads, so a rerun against other heads lands beside it, not on it."""
    canonical = json.dumps(sorted(sources.items()), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _capture(runner, args, cwd=None) -> tuple[int, str, str]:
    try:
        result = runner(
            list(args), cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False,
        )
    except OSError as error:
        return 127, "", str(error)
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def develop_heads(
    cedar_home, repositories: Iterable[str], runner=subprocess.run,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Every checked-out train repository's local `develop`, and which of them hold uncommitted work.

    A repository absent from this machine is left out rather than reported: the train captures it
    from GitHub, and the dispatch alignment check applies the same rule. Untracked files do not make
    a repository dirty, for the reason the train's own open-work check gives: they are ordinary
    while a developer works, and the train cannot ship them either way.
    """
    home = _home(cedar_home)
    heads: dict[str, str] = {}
    dirty: list[str] = []
    problems: list[str] = []
    for repository in repositories:
        root = home / repository
        if not (root / ".git").exists():
            continue
        code, head, detail = _capture(runner, ["git", "rev-parse", "refs/heads/develop"], cwd=root)
        if code != 0 or not head:
            problems.append(f"{repository} has no local develop branch"
                            + (f": {detail.splitlines()[-1]}" if detail else ""))
            continue
        heads[repository] = head
        code, status, _ = _capture(
            runner, ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root)
        if code == 0 and status:
            dirty.append(repository)
    return heads, dirty, problems


def stack_findings(cedar_home, runner=subprocess.run) -> list[str]:
    """Why the running stack cannot stand for its source, or nothing when it can.

    The controller's status is the only view that knows both whether a service answers and whether
    the process serving it predates its jar. A stale service passes every request and still makes a
    green run meaningless, and a frontend serving an Embeddable Editor other than the one its lock
    names is stale in exactly the same sense.
    """
    controller = _home(cedar_home) / CONTROLLER
    code, output, detail = _capture(runner, [str(controller), "status-tsv"])
    if code != 0:
        return ["cannot read native stack status"
                + (f": {detail.splitlines()[-1]}" if detail else "")]
    try:
        rows = ServerWorker.parse_native_status(output.splitlines())
    except ValueError as error:
        return [str(error)]
    findings = []
    for row in rows:
        service = row["service"]
        if row["pid"].startswith("!"):
            findings.append(f"{service} is served by a process the controller does not manage")
        elif row["health"] not in HEALTHY:
            findings.append(f"{service} is {row['health']}")
        if row["binary"] == "STALE":
            findings.append(f"{service} is stale: its process predates the code it should serve")
    findings.extend(source_currency_findings(
        cedar_home,
        [row["service"] for row in rows if row["binary"] in {"current", "STALE"}],
        runner=runner,
    ))
    return findings


def application_jar(cedar_home, service: str) -> Path | None:
    """The jar the controller would run for a microservice, or None when it has not been built."""
    target = (_home(cedar_home) / f"cedar-{service}-server"
              / f"cedar-{service}-server-application" / "target")
    jars = [
        path for path in target.glob(f"cedar-{service}-server-application-*.jar")
        if not path.name.startswith("original-")
    ]
    return max(jars, key=lambda path: path.stat().st_mtime) if jars else None


def source_currency_findings(cedar_home, services, runner=subprocess.run) -> list[str]:
    """Which deployed jars were written before the source the record will say they covered.

    The controller's `current` column compares a process with its jar, which answers whether a
    redeploy happened, not whether the jar holds the commit the record names. A jar written
    before its repository's develop head cannot contain that head, and a run recorded against
    it certifies code the stack is not running. That is the failure this module's own note
    called the operator's discipline; it is cheap enough to ask.
    """
    findings = []
    for service in services:
        repository = f"cedar-{service}-server"
        root = _home(cedar_home) / repository
        if not (root / ".git").exists():
            continue
        jar = application_jar(cedar_home, service)
        if jar is None:
            continue
        code, head, _detail = _capture(
            runner, ["git", "log", "-1", "--format=%ct %H", "refs/heads/develop"], cwd=root)
        if code != 0 or not head.strip():
            continue
        committed, _, revision = head.strip().partition(" ")
        try:
            committed_at = int(committed)
        except ValueError:
            continue
        if jar.stat().st_mtime < committed_at:
            findings.append(
                f"{service} was built before its source: its jar predates {repository} "
                f"develop {revision[:8]}"
            )
    return findings


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _stamp(moment: dt.datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


def run_smoke(
    cedar_home=None,
    *,
    runner=subprocess.run,
    clock: Callable[[], dt.datetime] = _now,
    environment: Mapping[str, str] | None = None,
) -> int:
    """Run both smoke tiers and record what they ran against. Zero only when both pass.

    The stack is judged before anything runs, and a refusal writes no record: an unhealthy or stale
    stack is a reason to stop, not evidence about the source. Both tiers run even when the first
    fails, so one command yields one complete answer.
    """
    home = _home(cedar_home or os.environ.get("CEDAR_HOME"))
    environment = dict(environment if environment is not None else os.environ)
    e2e = home / E2E
    if not (e2e / "package.json").exists():
        console.print(f"[red]{e2e} is not the e2e checkout; nothing to run[/red]")
        return 1
    if shutil.which("npm", path=environment.get("PATH")) is None:
        console.print("[red]npm is not on PATH; the smoke tiers need Node[/red]")
        return 1

    problems = stack_findings(home, runner)
    if problems:
        console.print("[red]The native stack cannot stand for its source:[/red]")
        for problem in problems:
            console.print(f"  - {problem}", markup=False)
        console.print("Bring every service to healthy and current, then rerun cedarcli test e2e.")
        return 1

    heads, dirty, head_problems = develop_heads(home, train_repositories(home), runner)
    if head_problems:
        console.print("[red]Source heads cannot be recorded:[/red]")
        for problem in head_problems:
            console.print(f"  - {problem}", markup=False)
        return 1
    if dirty:
        console.print(
            "[yellow]Uncommitted changes in: " + ", ".join(dirty)
            + ". The run is recorded, and the gate will refuse it for these repositories.[/yellow]")

    digest = sources_digest(heads)
    reports = reports_dir(home)
    reports.mkdir(parents=True, exist_ok=True)
    rest_report = reports / f"rest-smoke-{digest}.json"
    commands = {
        "rest": ["npm", "run", "smoke:rest", "--", f"--report={rest_report}"],
        "browser": ["npm", "run", "smoke"],
    }

    started = clock()
    tiers = {}
    for tier in TIERS:
        console.print(f"[bold]{tier} smoke[/bold]: {' '.join(commands[tier])}")
        tier_started = clock()
        try:
            result = runner(commands[tier], cwd=str(e2e), env=environment, check=False)
            code = result.returncode
        except OSError as error:
            console.print(f"[red]could not run the {tier} smoke: {error}[/red]")
            code = 127
        record = {
            "command": commands[tier],
            "exitCode": code,
            "verdict": "PASS" if code == 0 else "FAIL",
            "startedAt": _stamp(tier_started),
            "finishedAt": _stamp(clock()),
        }
        if tier == "rest":
            record.update(_rest_details(rest_report))
            if record["verdict"] == "PASS" and record.get("inventoryMatched") is False:
                record["verdict"] = "FAIL"
        tiers[tier] = record
        colour = "green" if record["verdict"] == "PASS" else "red"
        console.print(f"[{colour}]{tier} smoke: {record['verdict']}[/{colour}]")

    finished = clock()
    verdict = "PASS" if all(t["verdict"] == "PASS" for t in tiers.values()) else "FAIL"
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "verdict": verdict,
        "startedAt": _stamp(started),
        "finishedAt": _stamp(finished),
        "durationSeconds": round((finished - started).total_seconds(), 1),
        "sourcesDigest": digest,
        "sources": dict(sorted(heads.items())),
        "dirty": sorted(dirty),
        "tiers": tiers,
    }
    payload = json.dumps(report, indent=2) + "\n"
    (reports / f"{digest}.json").write_text(payload, encoding="utf-8")
    (reports / "latest.json").write_text(payload, encoding="utf-8")
    colour = "green" if verdict == "PASS" else "red"
    console.print(f"[{colour}]Whole-stack smoke: {verdict}[/{colour}]  ({len(heads)} sources, digest {digest})")
    console.print(f"Recorded: {reports / f'{digest}.json'}")
    return 0 if verdict == "PASS" else 1


def _rest_details(rest_report: Path) -> dict:
    """What the REST runner wrote about itself, or why it could not be read."""
    try:
        payload = json.loads(rest_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"report": str(rest_report), "reportError": str(error)}
    return {
        "report": str(rest_report),
        "runId": payload.get("runId"),
        "runnerVerdict": payload.get("verdict"),
        "counts": payload.get("counts"),
        "inventoryMatched": payload.get("inventoryMatched"),
    }


def read_report(cedar_home, expected: Mapping[str, str]) -> dict:
    """The run recorded for exactly these heads, else the latest run so the gate can say what moved."""
    reports = reports_dir(cedar_home)
    exact = reports / f"{sources_digest(expected)}.json"
    latest = reports / "latest.json"
    path = exact if exact.exists() else latest
    if not path.exists():
        raise SmokeGateError(f"no whole-stack smoke run is recorded; {REMEDY}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeGateError(f"the recorded smoke run at {path} cannot be read: {error}") from error
    if not isinstance(report, dict):
        raise SmokeGateError(f"the recorded smoke run at {path} is not a report")
    return report


def evaluate(report: Mapping, expected: Mapping[str, str]) -> list[str]:
    """Every reason the recorded run does not stand for this source, and nothing when it does."""
    findings: list[str] = []
    if report.get("schemaVersion") != SCHEMA_VERSION:
        return [f"the recorded smoke run has schema {report.get('schemaVersion')!r}, "
                f"not {SCHEMA_VERSION}; {REMEDY}"]
    when = report.get("finishedAt", "an unknown time")

    sources = report.get("sources") or {}
    unrecorded = [name for name in expected if name not in sources]
    moved = [name for name, head in expected.items()
             if name in sources and sources[name] != head]
    if unrecorded:
        findings.append(
            f"the smoke run at {when} recorded no source for {_names(unrecorded)}")
    if moved:
        if len(moved) <= SUMMARY_LIMIT:
            for name in moved:
                findings.append(
                    f"{name}: the smoke run at {when} tested {sources[name][:8]}, "
                    f"this source is {expected[name][:8]}")
        else:
            findings.append(
                f"the smoke run at {when} tested other commits of {len(moved)} repositories "
                f"({_names(moved)})")

    dirty = [name for name in report.get("dirty") or [] if name in expected]
    if dirty:
        findings.append(f"{_names(dirty)} had uncommitted changes when the smoke ran")

    tiers = report.get("tiers") or {}
    for tier in TIERS:
        record = tiers.get(tier)
        if not isinstance(record, dict):
            findings.append(f"the smoke run at {when} recorded no {tier} tier")
            continue
        if record.get("verdict") != "PASS":
            findings.append(f"the {tier} smoke was {record.get('verdict', 'unrecorded')} at "
                            f"{record.get('finishedAt', when)}")
        elif tier == "rest" and record.get("inventoryMatched") is False:
            findings.append("the REST smoke ran a different check inventory than the committed one")
    return findings


def findings_for(cedar_home, expected: Mapping[str, str]) -> list[str]:
    """Read and judge in one step, turning an unanswerable gate into its one finding."""
    try:
        report = read_report(cedar_home, expected)
    except SmokeGateError as error:
        return [str(error)]
    return evaluate(report, expected)


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:SUMMARY_LIMIT])
    if len(names) > SUMMARY_LIMIT:
        shown += f" and {len(names) - SUMMARY_LIMIT} more"
    return shown

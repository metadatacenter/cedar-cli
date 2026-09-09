"""CEDAR release lifecycle."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import copy
import dataclasses
import datetime as dt
import time
from org.metadatacenter.release_support.acceptance import (
    ReleaseAcceptance,
)
from org.metadatacenter.release_support.distribution import (
    ReleaseDistributionMaterializer,
)
from org.metadatacenter.release_support.errors import (
    ReleaseError,
    RetryableReleaseError,
)
from org.metadatacenter.release_support.integration import (
    ReleaseRemoteIntegrator,
)
from org.metadatacenter.release_support.output import (
    console,
)
from org.metadatacenter.release_support.policy import (
    ABANDONABLE_RELEASE_PHASES,
)
from org.metadatacenter.release_support.publication import (
    ReleaseArtifactPublisher,
)
from org.metadatacenter.release_support.refs import (
    ReleaseRefCreator,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.validation import (
    ReleaseBuildValidator,
)
from org.metadatacenter.release_support.versioning import (
    ReleaseVersionPreparer,
)
from org.metadatacenter.release_support.workspace import (
    ReleaseWorkspacePreparer,
)


def validate_active_release_builds(
    state: ReleaseState | None = None,
    validator: ReleaseBuildValidator | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "builds-validated":
        return manifest
    if manifest.get("phase") not in {
        "versions-prepared", "validating-builds", "build-validation-failed",
    }:
        raise ReleaseError(f"cannot validate builds while release is {manifest.get('phase')}")
    validator = validator or ReleaseBuildValidator(state)
    tasks = validator.tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("buildValidation") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "policy": {
            "releaseMavenTests": True,
            "nextDevelopmentMavenTests": False,
            "frontendProductionBuilds": True,
        },
        "completedTasks": {},
    })
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded build evidence does not match the current build plan")
    for record in completed.values():
        validator.verify_completed_task(record)
    evidence["attempt"] = int(evidence.get("attempt", 0)) + 1
    state.update_current_manifest({
        "phase": "validating-builds",
        "buildValidation": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        task = {**task, "evidenceAttempt": evidence["attempt"]}
        evidence["inProgressTask"] = task["id"]
        state.update_current_manifest({
            "phase": "validating-builds",
            "buildValidation": evidence,
            "failure": None,
        })
        console.print(
            f"Build {len(completed) + 1}/{len(tasks)}: {task['id']}", markup=False)
        try:
            record = validator.run_task(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = validator.failed_task_evidence(manifest, task)
            state.update_current_manifest({
                "phase": "build-validation-failed",
                "buildValidation": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        evidence.pop("inProgressTask", None)
        state.update_current_manifest({
            "phase": "validating-builds",
            "buildValidation": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "builds-validated",
        "buildValidation": evidence,
        "failure": None,
    })
    return completed_manifest


def create_active_release_refs(
    state: ReleaseState | None = None,
    creator: ReleaseRefCreator | None = None,
    materializer: ReleaseDistributionMaterializer | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "local-refs-created":
        return manifest
    if manifest.get("phase") not in {
        "builds-validated", "creating-local-refs", "local-ref-creation-failed",
    }:
        raise ReleaseError(f"cannot create local refs while release is {manifest.get('phase')}")
    creator = creator or ReleaseRefCreator(state)
    materializer = materializer or ReleaseDistributionMaterializer(
        state, git_runner=creator.git, environment=creator.environment,
    )
    manifest = materializer.materialize(manifest)
    tasks = creator.tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("localRefs") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pushed": False,
        "completedTasks": {},
    })
    if evidence.get("pushed") is not False:
        raise ReleaseError("local ref evidence must record that no refs were pushed")
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded local refs do not match the current ref plan")
    for record in completed.values():
        creator.verify_record(manifest, record)
    state.update_current_manifest({
        "phase": "creating-local-refs",
        "localRefs": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        evidence["inProgressTask"] = task["id"]
        state.update_current_manifest({
            "phase": "creating-local-refs",
            "localRefs": evidence,
            "failure": None,
        })
        console.print(
            f"Local ref {len(completed) + 1}/{len(tasks)}: {task['id']}", markup=False)
        try:
            record = creator.create(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = task
            state.update_current_manifest({
                "phase": "local-ref-creation-failed",
                "localRefs": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        evidence.pop("inProgressTask", None)
        state.update_current_manifest({
            "phase": "creating-local-refs",
            "localRefs": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "local-refs-created",
        "localRefs": evidence,
        "failure": None,
    })
    return completed_manifest


def integrate_active_release(
    state: ReleaseState | None = None,
    integrator: ReleaseRemoteIntegrator | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "remote-integrated":
        return manifest
    if manifest.get("phase") not in {
        "snapshots-published", "integrating-remotes", "remote-integration-failed",
    }:
        raise ReleaseError(f"cannot integrate remotes while release is {manifest.get('phase')}")
    integrator = integrator or ReleaseRemoteIntegrator(state)
    tasks = integrator.tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("remoteIntegration") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "completedTasks": {},
    })
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded remote integration does not match the release plan")
    for record in completed.values():
        integrator.verify_record(manifest, record)
    state.update_current_manifest({
        "phase": "integrating-remotes",
        "remoteIntegration": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        evidence["inProgressTask"] = task["id"]
        state.update_current_manifest({
            "phase": "integrating-remotes",
            "remoteIntegration": evidence,
            "failure": None,
        })
        console.print(
            f"Remote {len(completed) + 1}/{len(tasks)}: {task['id']}", markup=False)
        try:
            record = integrator.integrate(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = task["id"]
            state.update_current_manifest({
                "phase": "remote-integration-failed",
                "remoteIntegration": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        evidence.pop("inProgressTask", None)
        state.update_current_manifest({
            "phase": "integrating-remotes",
            "remoteIntegration": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "remote-integrated",
        "remoteIntegration": evidence,
        "failure": None,
    })
    return completed_manifest


def publish_active_release_snapshots(
    state: ReleaseState | None = None,
    publisher: ReleaseArtifactPublisher | None = None,
) -> dict:
    """Deploy the next-development snapshots before any remote learns the new version."""
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "snapshots-published":
        return manifest
    if manifest.get("phase") not in {
        "local-refs-created", "publishing-snapshots", "snapshot-publication-failed",
    }:
        raise ReleaseError(f"cannot publish snapshots while release is {manifest.get('phase')}")
    publisher = publisher or ReleaseArtifactPublisher(state)
    publisher.ensure_nexus_ready("snapshot publication")
    tasks = publisher.snapshot_tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("snapshotPublication") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "completedTasks": {},
    })
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded snapshot publication does not match the release plan")
    for record in completed.values():
        publisher.verify_record(manifest, record, tasks)
    state.update_current_manifest({
        "phase": "publishing-snapshots",
        "snapshotPublication": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        evidence["inProgressTask"] = task["id"]
        state.update_current_manifest({
            "phase": "publishing-snapshots",
            "snapshotPublication": evidence,
            "failure": None,
        })
        console.print(
            f"Snapshot {len(completed) + 1}/{len(tasks)}: {task['id']}", markup=False)
        try:
            record = publisher.run_task(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = task["id"]
            state.update_current_manifest({
                "phase": "snapshot-publication-failed",
                "snapshotPublication": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        evidence.pop("inProgressTask", None)
        state.update_current_manifest({
            "phase": "publishing-snapshots",
            "snapshotPublication": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "snapshots-published",
        "snapshotPublication": evidence,
        "failure": None,
    })
    return completed_manifest


def publish_active_release(
    state: ReleaseState | None = None,
    publisher: ReleaseArtifactPublisher | None = None,
    remote_integrator: ReleaseRemoteIntegrator | None = None,
    build_validator: ReleaseBuildValidator | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "artifacts-published":
        return manifest
    if manifest.get("phase") not in {
        "remote-integrated", "publishing-artifacts", "artifact-publication-failed",
    }:
        raise ReleaseError(f"cannot publish artifacts while release is {manifest.get('phase')}")
    publisher = publisher or ReleaseArtifactPublisher(state)
    publisher.ensure_nexus_ready("artifact publication")
    remote_integrator = remote_integrator or ReleaseRemoteIntegrator(state)
    for record in manifest.get("remoteIntegration", {}).get("completedTasks", {}).values():
        remote_integrator.verify_record(manifest, record)
    build_validator = build_validator or ReleaseBuildValidator(state)
    for record in manifest.get("buildValidation", {}).get("completedTasks", {}).values():
        build_validator.verify_completed_task(record)
    tasks = publisher.tasks(manifest)
    snapshot_tasks = publisher.snapshot_tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    snapshot_task_ids = {task["id"] for task in snapshot_tasks}
    evidence = copy.deepcopy(manifest.get("artifactPublication") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "completedTasks": {},
    })
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(
        task_ids | snapshot_task_ids
    ):
        raise ReleaseError("recorded artifact publication does not match the release plan")
    for record in completed.values():
        publisher.verify_record(
            manifest,
            record,
            snapshot_tasks if record.get("id") in snapshot_task_ids else tasks,
        )

    if hasattr(publisher, "_publication_base_progress_reporter"):
        previous_reporter = publisher._publication_base_progress_reporter
    else:
        previous_reporter = getattr(publisher, "progress_reporter", None)
        publisher._publication_base_progress_reporter = previous_reporter

    def record_progress(progress: dict) -> None:
        if previous_reporter is not None:
            previous_reporter(progress)
        evidence["inProgressTask"] = copy.deepcopy(progress)
        state.update_current_manifest({
            "phase": "publishing-artifacts",
            "artifactPublication": evidence,
            "failure": None,
        })

    publisher.progress_reporter = record_progress
    state.update_current_manifest({
        "phase": "publishing-artifacts",
        "artifactPublication": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        evidence["inProgressTask"] = {"id": task["id"], "kind": task["kind"]}
        state.update_current_manifest({
            "phase": "publishing-artifacts",
            "artifactPublication": evidence,
            "failure": None,
        })
        console.print(
            f"Artifact {len(set(completed) & task_ids) + 1}/{len(tasks)}: {task['id']}",
            markup=False,
        )
        try:
            record = publisher.run_task(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = task["id"]
            state.update_current_manifest({
                "phase": "artifact-publication-failed",
                "artifactPublication": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        evidence.pop("inProgressTask", None)
        state.update_current_manifest({
            "phase": "publishing-artifacts",
            "artifactPublication": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "artifacts-published",
        "artifactPublication": evidence,
        "failure": None,
    })
    return completed_manifest


def prepare_active_release(
    state: ReleaseState | None = None,
    preparer: ReleaseWorkspacePreparer | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "frontends-prepared":
        return manifest
    if manifest.get("phase") not in {"started", "frontend-preparation-failed"}:
        raise ReleaseError(f"cannot prepare frontends while release is {manifest.get('phase')}")
    preparer = preparer or ReleaseWorkspacePreparer(state)
    attempt = preparer.next_attempt(manifest["releaseVersion"])
    state.update_current_manifest({
        "phase": "preparing-frontends",
        "lastAttempt": str(attempt),
        "failure": None,
    })
    try:
        result = preparer.prepare(manifest, attempt)
    except ReleaseError as error:
        state.update_current_manifest({
            "phase": "frontend-preparation-failed",
            "lastAttempt": str(attempt),
            "failure": str(error),
        })
        raise
    completed, _ = state.update_current_manifest({
        "phase": "frontends-prepared",
        "frontendPreparation": result,
        "failure": None,
    })
    return completed


def prepare_active_release_versions(
    state: ReleaseState | None = None,
    preparer: ReleaseVersionPreparer | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "versions-prepared":
        return manifest
    if manifest.get("phase") != "frontends-prepared":
        raise ReleaseError(f"cannot stamp versions while release is {manifest.get('phase')}")
    preparer = preparer or ReleaseVersionPreparer(state)
    state.update_current_manifest({"phase": "preparing-versions", "failure": None})
    try:
        result = preparer.prepare(manifest)
    except ReleaseError as error:
        state.update_current_manifest({
            "phase": "version-preparation-failed",
            "failure": str(error),
        })
        raise
    completed, _ = state.update_current_manifest({
        "phase": "versions-prepared",
        "versionPreparation": result,
        "failure": None,
    })
    return completed


def accept_active_release(
    state: ReleaseState | None = None,
    acceptance: ReleaseAcceptance | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "accepted":
        state.conclude()
        return manifest
    if manifest.get("phase") not in {"artifacts-published", "acceptance-failed"}:
        raise ReleaseError(f"cannot accept a release that is {manifest.get('phase')}")
    acceptance = acceptance or ReleaseAcceptance(state)
    try:
        evidence = acceptance.run(manifest)
    except ReleaseError as error:
        state.update_current_manifest({
            "phase": "acceptance-failed",
            "failure": str(error),
        })
        raise
    completed_manifest, _ = state.update_current_manifest({
        "phase": "accepted",
        "acceptance": evidence,
        "failure": None,
    })
    state.conclude()
    return completed_manifest


def abandon_active_release(
    release_version: str,
    reason: str,
    state: ReleaseState | None = None,
) -> tuple[dict, Path]:
    """Conclude a local-only attempt while retaining its ledger and workspaces."""
    state = state or ReleaseState()
    current = state.read_current()
    if current.get("concludedAt"):
        raise ReleaseError(
            f"release {current.get('releaseVersion')} is already concluded as "
            f"{current.get('conclusion', 'accepted')}"
        )
    manifest, path = state.read_current_manifest()
    active = manifest.get("releaseVersion")
    if active != release_version:
        raise ReleaseError(
            f"active release is {active}, not the requested {release_version}"
        )
    reason = reason.strip()
    if not reason:
        raise ReleaseError("release abandonment requires a non-empty reason")
    phase = manifest.get("phase")
    external_evidence = any(
        manifest.get(section) is not None
        for section in ("snapshotPublication", "remoteIntegration", "artifactPublication")
    )
    local_refs = manifest.get("localRefs")
    pushed_local_refs = isinstance(local_refs, dict) and local_refs.get("pushed") is not False
    if phase not in ABANDONABLE_RELEASE_PHASES or external_evidence or pushed_local_refs:
        raise ReleaseError(
            f"cannot abandon release {release_version} from {phase}: publication or remote "
            "integration may already have changed external state; repair it and use "
            "cedarcli release resume"
        )
    abandoned_at = dt.datetime.now(dt.timezone.utc).isoformat()
    abandoned, path = state.update_current_manifest({
        "phase": "abandoned",
        "abandonment": {
            "abandonedAt": abandoned_at,
            "previousPhase": phase,
            "reason": reason,
        },
    })
    state.conclude("abandoned")
    return abandoned, path


@dataclasses.dataclass(frozen=True)
class ReleaseStage:
    """One step of the release: where it may start from, and what it records when it finishes.

    The stages were a chain of conditionals, each branch repeating the tail of the one below
    it, so adding a step meant editing every branch and forgetting one stranded a resumed
    release at that step. Ordering them instead makes resumption a search for the first stage
    that can still take the recorded phase, and adding a step a single entry in this list.
    """

    name: str
    entry_phases: frozenset[str]
    done_phase: str
    run: object

    def __call__(self, state: "ReleaseState", dependencies: dict) -> dict:
        return self.run(state, dependencies)


# A partial stamping attempt is evidence, not a state to resume into: the version preparer
# rewrites files across every repository, and continuing from half of that would stamp some
# twice. Both phases therefore rewind to the frontend stage, which starts a fresh attempt.
REWIND_TO_FRONTENDS = frozenset({"preparing-versions", "version-preparation-failed"})


RELEASE_STAGES = (
    ReleaseStage(
        "frontends",
        frozenset({"started", "preparing-frontends", "frontend-preparation-failed"}),
        "frontends-prepared",
        lambda state, deps: prepare_active_release(state, deps["workspace_preparer"]),
    ),
    ReleaseStage(
        "versions",
        frozenset({"frontends-prepared"}),
        "versions-prepared",
        lambda state, deps: prepare_active_release_versions(state, deps["version_preparer"]),
    ),
    ReleaseStage(
        "builds",
        frozenset({"versions-prepared", "validating-builds", "build-validation-failed"}),
        "builds-validated",
        lambda state, deps: validate_active_release_builds(state, deps["build_validator"]),
    ),
    ReleaseStage(
        "local-refs",
        frozenset({"builds-validated", "creating-local-refs", "local-ref-creation-failed"}),
        "local-refs-created",
        lambda state, deps: create_active_release_refs(state, deps["ref_creator"]),
    ),
    ReleaseStage(
        "snapshots",
        frozenset({
            "local-refs-created", "publishing-snapshots", "snapshot-publication-failed",
        }),
        "snapshots-published",
        lambda state, deps: publish_active_release_snapshots(
            state, deps["artifact_publisher"]),
    ),
    ReleaseStage(
        "remotes",
        frozenset({"snapshots-published", "integrating-remotes", "remote-integration-failed"}),
        "remote-integrated",
        lambda state, deps: integrate_active_release(state, deps["remote_integrator"]),
    ),
    ReleaseStage(
        "artifacts",
        frozenset({"remote-integrated", "publishing-artifacts", "artifact-publication-failed"}),
        "artifacts-published",
        lambda state, deps: publish_active_release(
            state, deps["artifact_publisher"], deps["remote_integrator"],
            deps["build_validator"],
        ),
    ),
    ReleaseStage(
        "acceptance",
        frozenset({"artifacts-published", "acceptance-failed"}),
        "accepted",
        lambda state, deps: accept_active_release(state, deps["acceptance"]),
    ),
)


RELEASE_TERMINAL_PHASE = RELEASE_STAGES[-1].done_phase


RELEASE_FINAL_PHASES = frozenset({RELEASE_TERMINAL_PHASE, "abandoned"})


def _next_release_stage(manifest: dict) -> str | None:
    phase = manifest.get("phase")
    if phase == RELEASE_TERMINAL_PHASE:
        return None
    if phase in REWIND_TO_FRONTENDS:
        return RELEASE_STAGES[0].name
    for stage in RELEASE_STAGES:
        if phase in stage.entry_phases:
            return stage.name
    raise ReleaseError(f"a release in {phase} has no stage that can continue it")


def _release_stage_has_finished(manifest: dict, name: str) -> bool:
    next_name = _next_release_stage(manifest)
    if next_name is None:
        return True
    order = [stage.name for stage in RELEASE_STAGES]
    return order.index(name) < order.index(next_name)


def advance_active_release(
    state: ReleaseState | None = None,
    workspace_preparer: ReleaseWorkspacePreparer | None = None,
    version_preparer: ReleaseVersionPreparer | None = None,
    build_validator: ReleaseBuildValidator | None = None,
    ref_creator: ReleaseRefCreator | None = None,
    remote_integrator: ReleaseRemoteIntegrator | None = None,
    artifact_publisher: ReleaseArtifactPublisher | None = None,
    acceptance: ReleaseAcceptance | None = None,
) -> dict:
    """Run the release from the first stage that can still take its recorded phase."""
    state = state or ReleaseState()
    dependencies = {
        "workspace_preparer": workspace_preparer,
        "version_preparer": version_preparer,
        "build_validator": build_validator,
        "ref_creator": ref_creator,
        "remote_integrator": remote_integrator,
        "artifact_publisher": artifact_publisher,
        "acceptance": acceptance,
    }
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") in REWIND_TO_FRONTENDS:
        manifest, _ = state.update_current_manifest({"phase": "frontend-preparation-failed"})
    phase = manifest.get("phase")
    if phase == "abandoned":
        raise ReleaseError(
            f"release {manifest.get('releaseVersion')} was abandoned and cannot be resumed"
        )
    if phase == RELEASE_TERMINAL_PHASE:
        # Acceptance writes the terminal manifest before it releases the active slot. A
        # process interruption between those two durable writes is repaired by resume.
        state.conclude()
        return manifest
    start = next(
        (index for index, stage in enumerate(RELEASE_STAGES) if phase in stage.entry_phases),
        None,
    )
    if start is None:
        raise ReleaseError(f"a release in {phase} has no stage that can continue it")
    for stage in RELEASE_STAGES[start:]:
        console.print(f"Release phase: {stage.name}", markup=False)
        manifest = stage(state, dependencies)
    return manifest


TRANSIENT_RETRY_ATTEMPTS = 5


TRANSIENT_RETRY_BACKOFF_SECONDS = (30, 60, 120, 300)


def _drive_release(
    state: ReleaseState, *, sleeper=time.sleep, verbose: bool = False,
) -> dict:
    """Advance the release, absorbing narrowly classified transport faults.

    A release runs for hours across two registries and forty remotes. Only
    RetryableReleaseError is retried, and only a safe transport condition raises it, so a
    guard still stops the release on its first refusal.
    """
    workspace_preparer = ReleaseWorkspacePreparer(state, verbose=verbose)
    version_preparer = ReleaseVersionPreparer(
        state, workspace_preparer=workspace_preparer)
    build_validator = ReleaseBuildValidator(state, verbose=verbose)
    ref_creator = ReleaseRefCreator(state, git_runner=workspace_preparer)
    remote_integrator = ReleaseRemoteIntegrator(
        state, git_runner=workspace_preparer)
    artifact_publisher = ReleaseArtifactPublisher(state, verbose=verbose)
    acceptance = ReleaseAcceptance(
        state, remote_integrator=remote_integrator, publisher=artifact_publisher)
    for attempt in range(1, TRANSIENT_RETRY_ATTEMPTS + 1):
        if attempt > 1:
            state.update_current_manifest({"retry": None, "failure": None})
        try:
            return advance_active_release(
                state,
                workspace_preparer=workspace_preparer,
                version_preparer=version_preparer,
                build_validator=build_validator,
                ref_creator=ref_creator,
                remote_integrator=remote_integrator,
                artifact_publisher=artifact_publisher,
                acceptance=acceptance,
            )
        except RetryableReleaseError as error:
            if attempt == TRANSIENT_RETRY_ATTEMPTS:
                raise
            delay = TRANSIENT_RETRY_BACKOFF_SECONDS[
                min(attempt - 1, len(TRANSIENT_RETRY_BACKOFF_SECONDS) - 1)]
            console.print(
                f"[yellow]Transient failure on attempt {attempt}; retrying in {delay}s[/yellow]")
            state.update_current_manifest({
                "retry": {
                    "attempt": attempt,
                    "maximum": TRANSIENT_RETRY_ATTEMPTS,
                    "delaySeconds": delay,
                    "reason": str(error),
                    "recordedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                },
            })
            sleeper(delay)
    raise ReleaseError("release exhausted its transient retries")

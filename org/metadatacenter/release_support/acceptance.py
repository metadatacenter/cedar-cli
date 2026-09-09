"""CEDAR release acceptance."""
from __future__ import annotations
from org.metadatacenter.util.SubprocessDiagnostics import describe_subprocess_failure
from pathlib import Path, PurePosixPath
import datetime as dt
import os
import subprocess
import sys
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.integration import (
    ReleaseRemoteIntegrator,
)
from org.metadatacenter.release_support.publication import (
    ReleaseArtifactPublisher,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)


def _publication_evidence_by_plan(
    manifest: dict,
    publisher: ReleaseArtifactPublisher,
    *,
    require_complete: bool,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Split publication evidence by task plan, including pre-split release ledgers.

    Early ledgers stored the next-development snapshot tasks in ``artifactPublication``.
    The phase was later split so snapshots could precede remote integration. Task identity,
    rather than the containing field, is therefore the durable way to interpret evidence.
    """
    release_tasks = publisher.tasks(manifest)
    snapshot_tasks = publisher.snapshot_tasks(manifest)
    release_ids = {task["id"] for task in release_tasks}
    snapshot_ids = {task["id"] for task in snapshot_tasks}
    if release_ids & snapshot_ids:
        raise ReleaseError("release and snapshot publication plans overlap")
    records: dict[str, dict] = {}
    for field in ("artifactPublication", "snapshotPublication"):
        section = manifest.get(field) or {}
        if not isinstance(section, dict):
            raise ReleaseError(f"{field} is not an object")
        completed = section.get("completedTasks", {})
        if not isinstance(completed, dict):
            raise ReleaseError(f"{field} completedTasks is not an object")
        for key, record in completed.items():
            if not isinstance(record, dict):
                raise ReleaseError(f"recorded publication task {key} is not an object")
            identifier = record.get("id", key)
            if identifier != key:
                raise ReleaseError(f"recorded publication task key differs from {identifier}")
            if identifier in records:
                raise ReleaseError(f"recorded publication task appears twice: {identifier}")
            records[identifier] = record
    unknown = set(records) - release_ids - snapshot_ids
    if unknown:
        raise ReleaseError(
            "recorded publication tasks no longer exist: " + ", ".join(sorted(unknown)))
    if require_complete:
        missing_release = release_ids - set(records)
        missing_snapshots = snapshot_ids - set(records)
        if missing_release or missing_snapshots:
            missing = sorted(missing_release | missing_snapshots)
            raise ReleaseError("release publication evidence is incomplete: " + ", ".join(missing))
    release_records = [records[task["id"]] for task in release_tasks if task["id"] in records]
    snapshot_records = [records[task["id"]] for task in snapshot_tasks if task["id"] in records]
    return release_tasks, release_records, snapshot_tasks, snapshot_records


class ReleaseAcceptance:
    """Prove a finished release from outside the ledger that recorded it.

    Each phase verifies its own work as it goes, but nothing until now asked whether the
    release as a whole holds once every phase has run. That question was answered by hand
    for 2.9.3, and an answer given by hand is one a release cannot be left alone to reach.
    """

    def __init__(
        self,
        state: ReleaseState,
        *,
        remote_integrator: "ReleaseRemoteIntegrator | None" = None,
        publisher: "ReleaseArtifactPublisher | None" = None,
        development_validator=None,
        environment=None,
    ):
        self.state = state
        self.environment = dict(os.environ if environment is None else environment)
        self.remote_integrator = remote_integrator or ReleaseRemoteIntegrator(
            state, environment=self.environment)
        self.publisher = publisher or ReleaseArtifactPublisher(
            state, environment=self.environment)
        self.development_validator = (
            development_validator or self._run_development_validator
        )

    def _check(self, name: str, detail: str) -> dict:
        return {"check": name, "detail": detail}

    def _remote_state_still_holds(self, manifest: dict) -> list[dict]:
        records = manifest.get("remoteIntegration", {}).get("completedTasks", {})
        expected = {task["id"] for task in self.remote_integrator.tasks(manifest)}
        if not isinstance(records, dict) or set(records) != expected:
            missing = expected - set(records) if isinstance(records, dict) else expected
            extra = set(records) - expected if isinstance(records, dict) else set()
            detail = []
            if missing:
                detail.append("missing " + ", ".join(sorted(missing)))
            if extra:
                detail.append("unknown " + ", ".join(sorted(extra)))
            raise ReleaseError("remote integration evidence is incomplete: " + "; ".join(detail))
        for record in records.values():
            self.remote_integrator.verify_record(manifest, record)
        tag = f"release-{manifest['releaseVersion']}"
        return [
            self._check(
                "remote-integration",
                f"{len(records)} repositories still carry their exact integrated refs",
            ),
            self._check(
                "release-tag",
                f"{tag} present at the recorded commit in all {len(records)} repositories",
            ),
        ]

    def _published_artifacts_still_hold(self, manifest: dict) -> list[dict]:
        release_tasks, records, snapshot_tasks, snapshots = _publication_evidence_by_plan(
            manifest, self.publisher, require_complete=True)
        for record in records:
            self.publisher.verify_record(manifest, record, release_tasks)
        for record in snapshots:
            self.publisher.verify_record(manifest, record, snapshot_tasks)
        return [self._check(
            "artifact-publication",
            f"{len(records)} release and {len(snapshots)} snapshot publication tasks still "
            "match their published bytes")]

    def _consumers_pin_the_proven_cee(self, manifest: dict) -> list[dict]:
        cee = manifest["cee"]
        expected = cee["public"]["version"]
        consumers = cee.get("consumers", [])
        for consumer in consumers:
            repository = consumer["repository"]
            if repository not in manifest.get("releaseRepositories", []):
                continue
            record = manifest.get("remoteIntegration", {}).get("completedTasks", {})
            if not any(item.get("repository") == repository for item in record.values()):
                raise ReleaseError(f"{repository} was never integrated, so its CEE pin is unproven")
        checks = [self._check(
            "cee-pin",
            f"{len(consumers)} consumer(s) pin the proven public CEE {expected}")]
        surfaces = manifest.get("publicationPlan", {}).get("npm", {}).get("surfaces", [])
        openview = next((
            surface for surface in surfaces
            if surface.get("id") == "openview" and isinstance(surface.get("ceeRuntime"), dict)
        ), None)
        if openview is None:
            return checks
        distribution_id = "release:npm:openview:distribution"
        distribution = manifest.get("distributionMaterialization", {}).get(
            "completedTasks", {}).get(distribution_id)
        cee_runtime = distribution.get("ceeRuntime") if isinstance(distribution, dict) else None
        if not isinstance(cee_runtime, dict) or cee_runtime.get("version") != expected:
            raise ReleaseError("OpenView has no proven runtime CEE distribution")
        publication = manifest.get("artifactPublication", {}).get(
            "completedTasks", {}).get("npm:release:openview")
        runtime_files = publication.get("runtimeFiles") if isinstance(publication, dict) else None
        relative = openview["ceeRuntime"].get("distribution")
        if (
            not isinstance(runtime_files, dict)
            or runtime_files.get(relative) != cee_runtime.get("servedBundleSha256")
        ):
            raise ReleaseError("published OpenView artifact does not contain its proven runtime CEE")
        checks.append(self._check(
            "openview-cee-runtime",
            f"OpenView distribution and npm artifact contain the normalized CEE {expected}",
        ))
        return checks

    def _run_development_validator(self, manifest: dict) -> str:
        workspace = Path(
            manifest.get("versionPreparation", {})
            .get("nextDevelopment", {})
            .get("workspace", "")
        )
        script = workspace / "cedar-development" / "ops" / "build_train.py"
        configuration = workspace / "cedar-development" / "ops"
        required = [
            script,
            configuration / "build-train.json",
            configuration / "frontend-train.json",
            configuration / "docker-train.json",
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise ReleaseError(
                "next-development train validation inputs are missing: " + ", ".join(missing)
            )
        command = [
            sys.executable,
            str(script),
            "--config", str(configuration / "build-train.json"),
            "validate-local",
            "--workspace", str(workspace),
            "--frontend-config", str(configuration / "frontend-train.json"),
            "--docker-config", str(configuration / "docker-train.json"),
            "--expected-source-version", manifest["nextDevelopmentVersion"],
        ]
        environment = dict(self.environment)
        environment.update({
            "CEDAR_HOME": str(workspace),
            "CI": "true",
            "NG_CLI_ANALYTICS": "false",
        })
        try:
            result = subprocess.run(
                command, check=False, text=True, capture_output=True, env=environment,
            )
        except OSError as error:
            raise ReleaseError(f"cannot validate next-development train state: {error}") from error
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part and part.strip()
        )
        if result.returncode:
            raise ReleaseError(
                "next-development train validation "
                f"{describe_subprocess_failure(result.returncode)}: {output}"
            )
        return output.splitlines()[-1] if output else "local train configuration passed"

    def _next_development_can_seed_train(self, manifest: dict) -> list[dict]:
        detail = self.development_validator(manifest)
        return [self._check(
            "next-development-train",
            f"{manifest['nextDevelopmentVersion']} can seed the next train: {detail}",
        )]

    def run(self, manifest: dict) -> dict:
        self.publisher.ensure_nexus_ready("release acceptance")
        checks = []
        checks.extend(self._remote_state_still_holds(manifest))
        checks.extend(self._published_artifacts_still_hold(manifest))
        checks.extend(self._consumers_pin_the_proven_cee(manifest))
        checks.extend(self._next_development_can_seed_train(manifest))
        return {
            "acceptedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "checks": checks,
        }

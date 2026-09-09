"""CEDAR release distribution."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import copy
import datetime as dt
import os
import shutil
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _directory_file_hashes,
    _file_sha256,
    _sha256,
)
from org.metadatacenter.release_support.policy import (
    LICENSE_FILE_NAME,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.validation import (
    ReleaseBuildValidator,
)
from org.metadatacenter.release_support.workspace import (
    ReleaseWorkspacePreparer,
)


class ReleaseDistributionMaterializer:
    """Make validated frontend builds the distributions committed by the release refs."""

    def __init__(self, state: ReleaseState, git_runner=None, environment=None):
        self.state = state
        self.environment = dict(os.environ if environment is None else environment)
        self.git = git_runner or ReleaseWorkspacePreparer(
            state, environment=self.environment,
        )

    @staticmethod
    def _under(relative: str, directory: str) -> bool:
        path = PurePosixPath(relative)
        parent = PurePosixPath(directory)
        return path == parent or parent in path.parents

    def _working_changes(self, root: Path) -> set[str]:
        changed = set(filter(None, self.git._run([
            "git", "-C", str(root), "diff", "--name-only", "HEAD", "--",
        ]).splitlines()))
        untracked = set(filter(None, self.git._run([
            "git", "-C", str(root), "ls-files", "--others", "--exclude-standard",
        ]).splitlines()))
        return changed | untracked

    @staticmethod
    def _verify_expected_files(root: Path, expected: dict[str, str | None]) -> None:
        for relative, digest in expected.items():
            path = root / relative
            if digest is None:
                if path.exists() or path.is_symlink():
                    raise ReleaseError(f"prepared deleted release file returned: {path}")
            elif _file_sha256(path) != digest:
                raise ReleaseError(f"prepared release file changed after validation: {path}")

    @staticmethod
    def _copy_exact_build(source: Path, destination: Path, preserve: list[str]) -> None:
        if not source.is_dir() or not destination.is_dir():
            raise ReleaseError(f"frontend distribution path is missing: {source} -> {destination}")
        preserved = {}
        for relative in preserve:
            safe = PurePosixPath(relative)
            if safe.is_absolute() or ".." in safe.parts:
                raise ReleaseError(f"unsafe preserved distribution path: {relative}")
            path = destination / relative
            if (
                relative == LICENSE_FILE_NAME
                and not path.exists()
                and not path.is_symlink()
            ):
                repository_license = destination.parent / LICENSE_FILE_NAME
                if repository_license.is_file() and not repository_license.is_symlink():
                    path = repository_license
            if not path.is_file() or path.is_symlink():
                raise ReleaseError(f"preserved distribution file is missing: {path}")
            preserved[relative] = path.read_bytes()
        collisions = set(preserved) & set(_directory_file_hashes(source))
        if collisions:
            raise ReleaseError(
                "frontend build overwrites preserved package metadata: "
                + ", ".join(sorted(collisions))
            )
        for child in destination.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for relative, content in preserved.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for child in source.iterdir():
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)

    @staticmethod
    def _cee_evidence(manifest: dict, root: Path, destination: Path, task: dict) -> dict | None:
        config = task.get("ceeRuntime")
        if not isinstance(config, dict):
            return None
        source = root / config.get("source", "")
        relative = config.get("distribution")
        if not isinstance(relative, str) or not relative:
            raise ReleaseError("OpenView CEE runtime distribution path is invalid")
        served = destination / relative
        source_bytes = source.read_bytes() if source.is_file() else None
        if source_bytes is None:
            raise ReleaseError(f"installed public CEE bundle is missing: {source}")
        expected_public = manifest.get("cee", {}).get(
            "promotionProof", {}).get("publicBundleSha256")
        if not isinstance(expected_public, str) or _sha256(source_bytes) != expected_public:
            raise ReleaseError("installed OpenView CEE does not match the proven public bundle")
        expected_served = source_bytes
        replacements = config.get("replacements", [])
        if not isinstance(replacements, list):
            raise ReleaseError("OpenView CEE production replacement plan is invalid")
        for replacement in replacements:
            if (
                not isinstance(replacement, list) or len(replacement) != 2
                or not all(isinstance(item, str) for item in replacement)
            ):
                raise ReleaseError("OpenView CEE production replacement is invalid")
            expected_served = expected_served.replace(
                replacement[0].encode(), replacement[1].encode())
        if not served.is_file() or served.read_bytes() != expected_served:
            raise ReleaseError(
                "OpenView build does not serve the proven public CEE after production normalization"
            )
        return {
            "version": manifest["cee"]["public"]["version"],
            "source": config["source"],
            "distribution": relative,
            "publicBundleSha256": expected_public,
            "servedBundleSha256": _sha256(expected_served),
        }

    def tasks(self, manifest: dict) -> list[dict]:
        surfaces = manifest.get("publicationPlan", {}).get("npm", {}).get("surfaces", [])
        if not isinstance(surfaces, list):
            raise ReleaseError("release manifest has no npm distribution plan")
        build_surfaces = [surface for surface in surfaces if surface.get("buildOutput")]
        if not build_surfaces:
            return []
        tasks = []
        for variant in ("release", "nextDevelopment"):
            workspace = Path(manifest["versionPreparation"][variant]["workspace"])
            for surface in build_surfaces:
                task = copy.deepcopy(surface)
                task.update({
                    "id": f"{variant}:npm:{surface['id']}:distribution",
                    "variant": variant,
                    "workspace": str(workspace),
                    "buildEvidenceId": f"{variant}:npm:{surface['id']}:build",
                })
                tasks.append(task)
        return tasks

    def verify_record(self, manifest: dict, record: dict) -> None:
        task = next((item for item in self.tasks(manifest) if item["id"] == record.get("id")), None)
        if task is None:
            raise ReleaseError(f"recorded distribution task no longer exists: {record.get('id')}")
        build = manifest.get("buildValidation", {}).get(
            "completedTasks", {}).get(task["buildEvidenceId"])
        if not isinstance(build, dict):
            raise ReleaseError(f"release has no build proof for {task['id']}")
        ReleaseBuildValidator.verify_completed_task(build)
        root = Path(task["workspace"]) / task["repository"]
        destination = root / task["directory"]
        if _directory_file_hashes(destination) != record.get("destinationFiles"):
            raise ReleaseError(f"materialized distribution changed for {task['id']}")
        cee = self._cee_evidence(manifest, root, destination, task)
        if cee != record.get("ceeRuntime"):
            raise ReleaseError(f"materialized CEE evidence changed for {task['id']}")

    def materialize(self, manifest: dict) -> dict:
        tasks = self.tasks(manifest)
        existing = manifest.get("distributionMaterialization")
        if isinstance(existing, dict) and existing.get("completedAt"):
            records = existing.get("completedTasks", {})
            if not isinstance(records, dict) or set(records) != {task["id"] for task in tasks}:
                raise ReleaseError("recorded distributions do not match the current build plan")
            for record in records.values():
                self.verify_record(manifest, record)
            return manifest
        completed_refs = manifest.get("localRefs", {}).get("completedTasks", {})
        if completed_refs:
            raise ReleaseError("cannot materialize frontend distributions after local refs exist")

        versions = copy.deepcopy(manifest["versionPreparation"])
        build_records = manifest.get("buildValidation", {}).get("completedTasks", {})
        grouped: dict[tuple[str, str], list[dict]] = {}
        for task in tasks:
            grouped.setdefault((task["variant"], task["repository"]), []).append(task)
        records = {}
        for (variant, repository), repository_tasks in grouped.items():
            root = Path(repository_tasks[0]["workspace"]) / repository
            prepared = versions[variant]["repositories"].get(repository)
            if not isinstance(prepared, dict) or not isinstance(prepared.get("fileSha256"), dict):
                raise ReleaseError(f"release has no prepared version record for {variant}:{repository}")
            before = prepared["fileSha256"]
            self._verify_expected_files(root, before)
            prefixes = [task["directory"] for task in repository_tasks]
            outside = {
                relative for relative in self._working_changes(root)
                if not any(self._under(relative, prefix) for prefix in prefixes)
            }
            expected_outside = {
                relative for relative in before
                if not any(self._under(relative, prefix) for prefix in prefixes)
            }
            if outside != expected_outside:
                raise ReleaseError(
                    f"unexpected prepared files before distribution materialization in {repository}"
                )
            for task in repository_tasks:
                build = build_records.get(task["buildEvidenceId"])
                expected_output = str(root / task["buildOutput"])
                if not isinstance(build, dict) or build.get("buildOutput") != expected_output:
                    raise ReleaseError(f"release has no build-output proof for {task['id']}")
                ReleaseBuildValidator.verify_completed_task(build)
                destination = root / task["directory"]
                self._copy_exact_build(
                    Path(build["buildOutput"]), destination, task.get("preserveFiles", []),
                )
                record = {
                    **task,
                    "buildOutputFiles": build["outputFiles"],
                    "destinationFiles": _directory_file_hashes(destination),
                    "ceeRuntime": self._cee_evidence(
                        manifest, root, destination, task),
                    "materializedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
                records[task["id"]] = record
            actual = self._working_changes(root)
            expected_files = {
                relative: (_file_sha256(root / relative) if (root / relative).is_file() else None)
                for relative in sorted(actual)
            }
            self._verify_expected_files(root, expected_files)
            prepared["changedFiles"] = sorted(actual)
            prepared["fileSha256"] = expected_files

        evidence = {
            "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "completedTasks": records,
        }
        updated, _ = self.state.update_current_manifest({
            "versionPreparation": versions,
            "distributionMaterialization": evidence,
        })
        return updated

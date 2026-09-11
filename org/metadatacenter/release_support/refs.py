"""CEDAR release refs."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from pathlib import Path, PurePosixPath
import datetime as dt
import os
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _file_sha256,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.workspace import (
    ReleaseWorkspacePreparer,
)


class ReleaseRefCreator:
    """Create verified local release refs without pushing them to any remote."""

    def __init__(self, state: ReleaseState, git_runner=None, environment=None):
        self.state = state
        self.environment = dict(invocation_environment() if environment is None else environment)
        self.git = git_runner or ReleaseWorkspacePreparer(
            state, environment=self.environment,
        )

    @staticmethod
    def _expected_files(manifest: dict, variant: str, repository: str) -> dict[str, str]:
        records = manifest["versionPreparation"][variant]["repositories"]
        if repository in records:
            return dict(records[repository]["fileSha256"])
        if variant != "release":
            raise ReleaseError(f"next-development has no prepared record for {repository}")
        expected = {}
        for consumer in manifest["frontendPreparation"]["consumers"]:
            if consumer["repository"] != repository:
                continue
            expected[consumer["manifest"]] = consumer["manifestSha256"]
            expected[consumer["lock"]] = consumer["lockSha256"]
        if not expected:
            raise ReleaseError(f"release has no prepared source record for {repository}")
        return expected

    @staticmethod
    def _expected_changed_files(
        manifest: dict, variant: str, repository: str, expected_files: dict[str, str],
    ) -> list[str]:
        prepared = manifest["versionPreparation"][variant]["repositories"].get(repository)
        if isinstance(prepared, dict) and isinstance(prepared.get("changedFiles"), list):
            return list(prepared["changedFiles"])
        if variant == "release":
            frontend = manifest.get("frontendPreparation", {}).get(
                "repositories", {}).get(repository)
            if isinstance(frontend, dict) and isinstance(frontend.get("changedFiles"), list):
                return list(frontend["changedFiles"])
        # Compatibility for manifests created before changedFiles was recorded separately.
        return list(expected_files)

    def tasks(self, manifest: dict) -> list[dict]:
        release_repositories = list(manifest["releaseRepositories"])
        for consumer in manifest["cee"]["consumers"]:
            repository = consumer["repository"]
            if repository not in release_repositories:
                release_repositories.append(repository)
        tasks = []
        variants = manifest["versionPreparation"]
        for variant, repositories, branch, tag in (
            (
                "release",
                release_repositories,
                f"release/pre-{manifest['releaseVersion']}",
                f"release-{manifest['releaseVersion']}",
            ),
            (
                "nextDevelopment",
                manifest["releaseRepositories"],
                f"release/post-{manifest['nextDevelopmentVersion']}",
                None,
            ),
        ):
            workspace = Path(variants[variant]["workspace"])
            for repository in repositories:
                expected_files = self._expected_files(manifest, variant, repository)
                tasks.append({
                    "id": f"{variant}:{repository}",
                    "variant": variant,
                    "repository": repository,
                    "workspace": str(workspace),
                    "branch": branch,
                    "tag": tag,
                    "sourceRevision": manifest["sourceRepositories"][repository],
                    "expectedFiles": expected_files,
                    "expectedChangedFiles": self._expected_changed_files(
                        manifest, variant, repository, expected_files),
                })
        return tasks

    def _ref(self, root: Path, reference: str) -> str | None:
        try:
            return self.git._run([
                "git", "-C", str(root), "rev-parse", "--verify", reference,
            ])
        except ReleaseError:
            return None

    def _identity(self, root: Path) -> tuple[str, str]:
        name = self.environment.get("CEDAR_RELEASE_GIT_NAME")
        email = self.environment.get("CEDAR_RELEASE_GIT_EMAIL")
        if not name:
            try:
                name = self.git._run(["git", "-C", str(root), "config", "user.name"])
            except ReleaseError:
                name = None
        if not email:
            try:
                email = self.git._run(["git", "-C", str(root), "config", "user.email"])
            except ReleaseError:
                email = None
        if not name or not email:
            raise ReleaseError(
                "Git author identity is unavailable; configure git user.name/user.email or "
                "CEDAR_RELEASE_GIT_NAME/CEDAR_RELEASE_GIT_EMAIL"
            )
        return name, email

    def _working_changes(self, root: Path) -> set[str]:
        changed = set(filter(None, self.git._run([
            "git", "-C", str(root), "diff", "--name-only", "HEAD", "--",
        ]).splitlines()))
        untracked = set(filter(None, self.git._run([
            "git", "-C", str(root), "ls-files", "--others", "--exclude-standard",
        ]).splitlines()))
        return changed | untracked

    @staticmethod
    def _verify_file_hashes(root: Path, expected: dict[str, str | None]) -> None:
        for relative, digest in expected.items():
            path = root / relative
            if digest is None:
                if path.exists() or path.is_symlink():
                    raise ReleaseError(f"prepared deleted release file returned: {path}")
            elif _file_sha256(path) != digest:
                raise ReleaseError(f"prepared release file changed after validation: {root / relative}")

    def _verify_commit(self, root: Path, task: dict, commit: str) -> dict:
        source = task["sourceRevision"]
        parent = self.git._run(["git", "-C", str(root), "rev-parse", f"{commit}^"])
        if parent != source:
            raise ReleaseError(
                f"local {task['variant']} commit for {task['repository']} is not based on {source}"
            )
        changed = set(filter(None, self.git._run([
            "git", "-C", str(root), "diff", "--no-renames", "--name-only",
            source, commit, "--",
        ]).splitlines()))
        expected = set(task.get("expectedChangedFiles", task["expectedFiles"]))
        if changed != expected:
            raise ReleaseError(
                f"local {task['variant']} commit for {task['repository']} has wrong files: "
                f"actual={sorted(changed)}, expected={sorted(expected)}"
            )
        self._verify_file_hashes(root, task["expectedFiles"])
        tree = self.git._run(["git", "-C", str(root), "rev-parse", f"{commit}^{{tree}}"])
        return {
            "id": task["id"],
            "variant": task["variant"],
            "repository": task["repository"],
            "workspace": task["workspace"],
            "branch": task["branch"],
            "tag": task["tag"],
            "sourceRevision": source,
            "commit": commit,
            "tree": tree,
            "changedFiles": sorted(changed),
            "fileSha256": task["expectedFiles"],
        }

    def create(self, manifest: dict, task: dict) -> dict:
        root = Path(task["workspace"]) / task["repository"]
        if not (root / ".git").exists():
            raise ReleaseError(f"prepared repository clone is missing: {root}")
        source = task["sourceRevision"]
        branch_ref = f"refs/heads/{task['branch']}"
        branch_tip = self._ref(root, branch_ref)
        if branch_tip is None:
            head = self.git._run(["git", "-C", str(root), "rev-parse", "HEAD"])
            if head != source:
                raise ReleaseError(f"{root} is at {head}, expected source {source}")
            actual = self._working_changes(root)
            expected_changes = set(task.get("expectedChangedFiles", task["expectedFiles"]))
            if actual != expected_changes:
                raise ReleaseError(
                    f"prepared files changed before local commit in {task['repository']}: "
                    f"actual={sorted(actual)}, expected={sorted(expected_changes)}"
                )
            self._verify_file_hashes(root, task["expectedFiles"])
            self.git._run(["git", "-C", str(root), "switch", "--quiet", "-c", task["branch"]])
            branch_tip = source
        else:
            self.git._run(["git", "-C", str(root), "switch", "--quiet", task["branch"]])

        if branch_tip == source:
            actual = self._working_changes(root)
            expected_changes = set(task.get("expectedChangedFiles", task["expectedFiles"]))
            if actual != expected_changes:
                raise ReleaseError(
                    f"local branch has wrong prepared files for {task['repository']}"
                )
            self._verify_file_hashes(root, task["expectedFiles"])
            if expected_changes:
                self.git._run([
                    "git", "-C", str(root), "add", "--", *sorted(expected_changes),
                ])
            name, email = self._identity(root)
            message = (
                f"Prepare CEDAR {manifest['releaseVersion']} from train {manifest['train']}"
                if task["variant"] == "release"
                else (
                    f"Prepare CEDAR {manifest['nextDevelopmentVersion']} after "
                    f"{manifest['releaseVersion']}"
                )
            )
            self.git._run([
                "git", "-c", f"user.name={name}", "-c", f"user.email={email}",
                "-c", "commit.gpgSign=false", "-C", str(root),
                "commit", "--quiet", "--no-verify", "--allow-empty", "-m", message,
            ])
            branch_tip = self.git._run(["git", "-C", str(root), "rev-parse", "HEAD"])

        record = self._verify_commit(root, task, branch_tip)
        if self._working_changes(root):
            raise ReleaseError(f"local ref creation left tracked changes in {task['repository']}")
        if task["tag"]:
            tag_ref = f"refs/tags/{task['tag']}"
            tag_tip = self._ref(root, tag_ref)
            if tag_tip is None:
                self.git._run([
                    "git", "-C", str(root), "tag", task["tag"], branch_tip,
                ])
                tag_tip = self._ref(root, tag_ref)
            if tag_tip != branch_tip:
                raise ReleaseError(
                    f"local tag {task['tag']} does not identify the prepared release commit"
                )
        record["createdAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return record

    def verify_record(self, manifest: dict, record: dict) -> None:
        task = next((item for item in self.tasks(manifest) if item["id"] == record.get("id")), None)
        if task is None:
            raise ReleaseError(f"recorded local ref task no longer exists: {record.get('id')}")
        root = Path(task["workspace"]) / task["repository"]
        branch_tip = self._ref(root, f"refs/heads/{task['branch']}")
        if branch_tip != record.get("commit"):
            raise ReleaseError(f"recorded local branch changed for {task['repository']}")
        if task["tag"]:
            tag_tip = self._ref(root, f"refs/tags/{task['tag']}")
            if tag_tip != branch_tip:
                raise ReleaseError(f"recorded local tag changed for {task['repository']}")
        verified = self._verify_commit(root, task, branch_tip)
        if verified["tree"] != record.get("tree"):
            raise ReleaseError(f"recorded local tree changed for {task['repository']}")

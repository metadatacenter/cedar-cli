"""CEDAR release workspace."""
from __future__ import annotations
from org.metadatacenter.util.SubprocessDiagnostics import describe_subprocess_failure
from pathlib import Path, PurePosixPath
import datetime as dt
import json
import os
import re
import subprocess
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _file_sha256,
)
from org.metadatacenter.release_support.output import (
    console,
)
from org.metadatacenter.release_support.policy import (
    GIT_SHA_RE,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.transport import (
    _raise_command_failure,
)


class ReleaseWorkspacePreparer:
    """Prepare stable-CEE frontend inputs in clones of the train's exact commits."""

    def __init__(
        self, state: ReleaseState, command_runner=None, environment=None, *, verbose: bool = False,
    ):
        self.state = state
        self.command_runner = command_runner or subprocess.run
        self.environment = dict(os.environ if environment is None else environment)
        self.verbose = verbose

    def next_attempt(self, release_version: str) -> Path:
        attempts = self.state.root / "attempts" / release_version
        number = 1
        if attempts.is_dir():
            used = [
                int(path.name) for path in attempts.iterdir()
                if path.is_dir() and path.name.isdigit()
            ]
            if used:
                number = max(used) + 1
        return attempts / f"{number:03d}"

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        environment=None,
        stream: bool = False,
    ) -> str:
        try:
            result = self.command_runner(
                args,
                cwd=str(cwd) if cwd else None,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            raise ReleaseError(f"cannot run {args[0]}: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if result.returncode < 0 and not detail:
                detail = "the process produced no diagnostic output of its own"
            _raise_command_failure(
                args,
                f"command {describe_subprocess_failure(result.returncode)} "
                f"({' '.join(args)})",
                detail,
            )
        stdout = (result.stdout or "").strip()
        if stream:
            output = "\n".join(
                part for part in (stdout, (result.stderr or "").strip()) if part)
            if output and self.verbose:
                console.print(output, markup=False)
            return output
        return stdout

    def _clone(self, repository: str, revision: str, destination: Path) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", repository):
            raise ReleaseError(f"invalid train repository name {repository!r}")
        if not GIT_SHA_RE.fullmatch(revision or ""):
            raise ReleaseError(f"invalid train revision for {repository}: {revision!r}")
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        source = Path(cedar_home) / repository
        if not (source / ".git").exists():
            raise ReleaseError(f"local source repository is missing: {source}")
        self._run(["git", "-C", str(source), "cat-file", "-e", f"{revision}^{{commit}}"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run([
            "git", "clone", "--quiet", "--no-checkout", "--local",
            str(source), str(destination),
        ])
        self._run(["git", "-C", str(destination), "checkout", "--quiet", "--detach", revision])
        actual = self._run(["git", "-C", str(destination), "rev-parse", "HEAD"])
        if actual != revision:
            raise ReleaseError(f"isolated clone for {repository} is {actual}, expected {revision}")

    @staticmethod
    def _consumer_paths(workspace: Path, consumer: dict) -> tuple[Path, Path]:
        root = workspace / consumer["repository"]
        return root / consumer["manifest"], root / consumer["lock"]

    @staticmethod
    def _verify_consumer(workspace: Path, consumer: dict, public_cee: dict) -> dict:
        manifest_path, lock_path = ReleaseWorkspacePreparer._consumer_paths(workspace, consumer)
        try:
            package = json.loads(manifest_path.read_bytes())
            lock = json.loads(lock_path.read_bytes())
        except json.JSONDecodeError as error:
            raise ReleaseError(f"CEE consumer has invalid JSON: {error}") from error
        dependency = "cedar-embeddable-editor"
        cee_version = public_cee["version"]
        declared = package.get("dependencies", {}).get(dependency)
        locked = lock.get("packages", {}).get("", {}).get("dependencies", {}).get(dependency)
        installed = lock.get("packages", {}).get(f"node_modules/{dependency}", {})
        if declared != cee_version:
            raise ReleaseError(f"{manifest_path} pins {declared!r}, expected {cee_version}")
        if locked != cee_version:
            raise ReleaseError(f"{lock_path} root pins {locked!r}, expected {cee_version}")
        if installed.get("version") != cee_version:
            raise ReleaseError(f"{lock_path} installs the wrong CEE version")
        if installed.get("resolved") != public_cee["tarball"]:
            raise ReleaseError(f"{lock_path} does not resolve the proven public CEE tarball")
        if installed.get("integrity") != public_cee["integrity"]:
            raise ReleaseError(f"{lock_path} does not carry the proven public CEE integrity")
        return {
            "label": consumer["label"],
            "repository": consumer["repository"],
            "manifest": consumer["manifest"],
            "lock": consumer["lock"],
            "manifestSha256": _file_sha256(manifest_path),
            "lockSha256": _file_sha256(lock_path),
            "integrity": installed["integrity"],
            "resolved": installed["resolved"],
        }

    def prepare(self, manifest: dict, attempt: Path) -> dict:
        consumers = manifest.get("cee", {}).get("consumers", [])
        if not isinstance(consumers, list) or len(consumers) != 7:
            raise ReleaseError("release manifest does not contain all seven CEE consumers")
        repositories = manifest.get("sourceRepositories", {})
        if not isinstance(repositories, dict):
            raise ReleaseError("release manifest has no source repository inventory")
        workspace = attempt / "workspace"
        required_repositories = sorted(
            {consumer["repository"] for consumer in consumers} | {"cedar-development"}
        )
        for repository in required_repositories:
            revision = repositories.get(repository)
            self._clone(repository, revision, workspace / repository)

        before = {}
        for consumer in consumers:
            manifest_path, lock_path = self._consumer_paths(workspace, consumer)
            before[(consumer["repository"], consumer["manifest"])] = {
                "manifestSha256": _file_sha256(manifest_path),
                "lockSha256": _file_sha256(lock_path),
            }

        public_cee = manifest["cee"]["public"]
        cee_version = public_cee["version"]
        helper = workspace / "cedar-development" / "ops" / "propagate-cee-release.mjs"
        if not helper.is_file():
            raise ReleaseError(f"train release helper is missing: {helper}")
        command_environment = dict(self.environment)
        command_environment["CEDAR_HOME"] = str(workspace)
        command_environment["npm_config_cache"] = str(attempt / "npm-cache")
        command_environment["NPM_CONFIG_STRICT_ALLOW_SCRIPTS"] = "true"
        apply_output = self._run(
            ["node", str(helper), "--apply", cee_version],
            cwd=workspace / "cedar-development",
            environment=command_environment,
            stream=True,
        )
        check_output = self._run(
            ["node", str(helper), "--check", cee_version],
            cwd=workspace / "cedar-development",
            environment=command_environment,
            stream=True,
        )
        preparation_log = attempt / "preparation-logs" / "cee-propagation.log"
        preparation_log.parent.mkdir(parents=True, exist_ok=True)
        preparation_log.write_text(
            "\n".join(output for output in (apply_output, check_output) if output) + "\n",
            encoding="utf-8",
        )

        verified = []
        allowed_by_repo: dict[str, set[str]] = {}
        for consumer in consumers:
            record = self._verify_consumer(workspace, consumer, public_cee)
            record["before"] = before[(consumer["repository"], consumer["manifest"])]
            verified.append(record)
            allowed_by_repo.setdefault(consumer["repository"], set()).update({
                consumer["manifest"], consumer["lock"],
            })
        changes = {}
        for repository in required_repositories:
            root = workspace / repository
            changed = set(filter(None, self._run([
                "git", "-C", str(root), "diff", "--name-only", "HEAD", "--",
            ]).splitlines()))
            untracked = set(filter(None, self._run([
                "git", "-C", str(root), "ls-files", "--others", "--exclude-standard",
            ]).splitlines()))
            actual = changed | untracked
            allowed = allowed_by_repo.get(repository, set())
            unexpected = sorted(actual - allowed)
            if unexpected:
                raise ReleaseError(
                    f"CEE propagation changed unexpected files in {repository}: "
                    + ", ".join(unexpected)
                )
            changes[repository] = sorted(actual)
        return {
            "attempt": attempt.name,
            "log": str(preparation_log),
            "logSha256": _file_sha256(preparation_log),
            "workspace": str(workspace),
            "preparedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "repositories": {
                repository: {
                    "revision": repositories[repository],
                    "path": str(workspace / repository),
                    "changedFiles": changes[repository],
                }
                for repository in required_repositories
            },
            "consumers": verified,
        }

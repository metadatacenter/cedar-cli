"""CEDAR release validation."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError,
    executable_build_workspace,
    embedded_mongo_processes,
    require_no_embedded_mongo_processes,
    wait_for_no_embedded_mongo_processes,
)
from org.metadatacenter.util.SubprocessDiagnostics import describe_subprocess_failure
from pathlib import Path, PurePosixPath
import copy
import datetime as dt
from org.metadatacenter.util.ProcessRunner import run_process
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _directory_file_hashes,
    _file_sha256,
)
from org.metadatacenter.release_support.policy import (
    FRONTEND_BUILD_SURFACES,
)
from org.metadatacenter.release_support.state import (
    ReleaseState,
)
from org.metadatacenter.release_support.transport import (
    _raise_command_failure,
)


class ReleaseBuildValidator:
    """Build prepared source variants without publishing or changing Git history."""

    def __init__(
        self, state: ReleaseState, executor=None, environment=None, *, verbose: bool = False,
    ):
        self.state = state
        self.executor = executor
        self.environment = dict(invocation_environment() if environment is None else environment)
        self.verbose = verbose

    @staticmethod
    def _task_id(*parts: str) -> str:
        return ":".join(parts)

    def tasks(self, manifest: dict) -> list[dict]:
        versions = manifest.get("versionPreparation", {})
        phases = manifest.get("mavenPhases")
        if not isinstance(phases, list) or not phases:
            raise ReleaseError("release manifest has no ordered Maven build phases")
        tasks = []
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        for variant in ("release", "nextDevelopment"):
            variant_record = versions.get(variant, {})
            workspace = Path(variant_record.get("workspace", ""))
            if not workspace.is_dir():
                raise ReleaseError(f"prepared {variant} workspace is missing")
            local_repository = attempt / "build-cache" / variant / "m2" / "repository"
            for phase in phases:
                repository = phase["repository"]
                root = workspace / repository
                wrapper = root / "mvnw"
                if not wrapper.is_file():
                    raise ReleaseError(f"Maven wrapper is missing: {wrapper}")
                command = [
                    str(wrapper),
                    "--batch-mode",
                    "--no-transfer-progress",
                    f"-Dmaven.repo.local={local_repository}",
                    "clean",
                    "install",
                ]
                if variant == "nextDevelopment":
                    command.append("-DskipTests")
                tasks.append({
                    "id": self._task_id(variant, "maven", phase["name"]),
                    "variant": variant,
                    "kind": "maven",
                    "repository": repository,
                    "cwd": str(root),
                    "command": command,
                    "tests": variant == "release",
                })
            for surface in FRONTEND_BUILD_SURFACES:
                root = workspace / surface["repository"] / surface["directory"]
                if not root.is_dir():
                    if surface["repository"] not in manifest["releaseRepositories"]:
                        continue
                    raise ReleaseError(f"frontend build surface is missing: {root}")
                package = root / "package.json"
                lock = root / "package-lock.json"
                if not package.is_file() or not lock.is_file():
                    raise ReleaseError(f"frontend build surface has no package and lock: {root}")
                install = ["npm", "ci", *surface["install"]]
                tasks.append({
                    "id": self._task_id(variant, "npm", surface["id"], "install"),
                    "variant": variant,
                    "kind": "npm-install",
                    "repository": surface["repository"],
                    "cwd": str(root),
                    "command": install,
                    "tests": False,
                })
                if surface["build"]:
                    build_task = {
                        "id": self._task_id(variant, "npm", surface["id"], "build"),
                        "variant": variant,
                        "kind": "frontend-build",
                        "repository": surface["repository"],
                        "cwd": str(root),
                        "command": surface["build"],
                        "tests": False,
                    }
                    if surface.get("buildOutput"):
                        build_task["buildOutput"] = str(
                            workspace / surface["repository"] / surface["buildOutput"]
                        )
                    tasks.append(build_task)
        identifiers = [task["id"] for task in tasks]
        if len(identifiers) != len(set(identifiers)):
            raise ReleaseError("release build plan contains duplicate task identifiers")
        return tasks

    @staticmethod
    def _stream_command(
        command: list[str], cwd: Path, environment: dict, log: Path, *, verbose: bool = False,
    ) -> None:
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log.open("w", encoding="utf-8") as output:
                def report(line):
                    output.write(line + "\n")
                    output.flush()
                    if verbose:
                        print(line, flush=True)
                result = run_process(command, cwd=str(cwd), env=environment, on_line=report)
                returncode = result.returncode
        except OSError as error:
            raise ReleaseError(f"cannot run {command[0]}: {error}") from error
        if returncode:
            try:
                detail = "\n".join(log.read_text(encoding="utf-8").splitlines()[-80:])
            except OSError:
                detail = ""
            if returncode < 0 and not detail:
                detail = "the process produced no diagnostic output of its own"
            _raise_command_failure(
                command,
                f"build command {describe_subprocess_failure(returncode)}: "
                f"{' '.join(command)}; log: {log}",
                detail,
            )

    def run_task(self, manifest: dict, task: dict) -> dict:
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        evidence_attempt = task.get("evidenceAttempt", 1)
        log = (
            attempt / "build-logs" / f"attempt-{evidence_attempt:03d}"
            / f"{task['id'].replace(':', '-')}.log"
        )
        environment = dict(self.environment)
        environment["CEDAR_HOME"] = str(
            manifest["versionPreparation"][task["variant"]]["workspace"]
        )
        environment["npm_config_cache"] = str(attempt / "build-cache" / "npm")
        environment["NPM_CONFIG_STRICT_ALLOW_SCRIPTS"] = "true"
        environment["CI"] = "true"
        environment["NG_CLI_ANALYTICS"] = "false"
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        guarded_maven = task.get("kind") == "maven" and task.get("tests") is True
        if guarded_maven:
            try:
                require_no_embedded_mongo_processes(
                    f"release Maven task {task['id']}")
            except BuildSafetyError as error:
                raise ReleaseError(str(error)) from error
        try:
            with executable_build_workspace(
                environment, java=task.get("kind") == "maven",
            ) as (_, environment):
                command_failure = None
                try:
                    if self.executor is None:
                        self._stream_command(
                            task["command"], Path(task["cwd"]), environment, log,
                            verbose=self.verbose,
                        )
                    else:
                        log.parent.mkdir(parents=True, exist_ok=True)
                        output = self.executor(task, environment)
                        log.write_text(output or "", encoding="utf-8")
                except ReleaseError as error:
                    command_failure = error
                if guarded_maven:
                    try:
                        wait_for_no_embedded_mongo_processes(
                            f"completion of release Maven task {task['id']}")
                    except BuildSafetyError as error:
                        if command_failure is not None:
                            raise ReleaseError(f"{command_failure}\n{error}") from command_failure
                        raise ReleaseError(str(error)) from error
                if command_failure is not None:
                    raise command_failure
        except BuildSafetyError as error:
            raise ReleaseError(str(error)) from error
        record = {
            **task,
            "startedAt": started,
            "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "log": str(log),
            "logSha256": _file_sha256(log),
        }
        if task.get("buildOutput"):
            record["outputFiles"] = _directory_file_hashes(Path(task["buildOutput"]))
        return record

    @staticmethod
    def failed_task_evidence(manifest: dict, task: dict) -> dict:
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        evidence_attempt = task.get("evidenceAttempt", 1)
        log = (
            attempt / "build-logs" / f"attempt-{evidence_attempt:03d}"
            / f"{task['id'].replace(':', '-')}.log"
        )
        result = copy.deepcopy(task)
        if log.is_file():
            result["log"] = str(log)
            result["logSha256"] = _file_sha256(log)
        return result

    @staticmethod
    def verify_completed_task(record: dict) -> None:
        log = Path(record.get("log", ""))
        expected = record.get("logSha256")
        if not log.is_file() or not isinstance(expected, str) or _file_sha256(log) != expected:
            raise ReleaseError(f"completed build evidence is missing or changed for {record.get('id')}")
        if record.get("buildOutput"):
            expected_files = record.get("outputFiles")
            if not isinstance(expected_files, dict) or not expected_files:
                raise ReleaseError(f"completed build has no output evidence for {record.get('id')}")
            if _directory_file_hashes(Path(record["buildOutput"])) != expected_files:
                raise ReleaseError(f"completed build output changed for {record.get('id')}")

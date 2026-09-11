from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment

import contextlib
import dataclasses
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time


FRONTEND_RUNTIME_MARKERS = (
    "ng serve",
    "npm run start",
    "npm start",
    "gulp serve",
    "gulp watch",
    "vite",
    "webpack serve",
)


class BuildSafetyError(RuntimeError):
    pass


@contextlib.contextmanager
def executable_build_workspace(environment=None, *, java=False):
    """Private, executable scratch space scoped to build children, never the host."""
    environment = dict(invocation_environment() if environment is None else environment)
    configured = environment.get('CEDAR_BUILD_TMPDIR')
    home = environment.get('CEDAR_HOME')
    if not configured and not home:
        raise BuildSafetyError('CEDAR_HOME is required for build temporary storage')
    root = Path(configured) if configured else Path(home) / '.cedar' / 'build-tmp'
    if not root.is_absolute():
        raise BuildSafetyError('CEDAR_BUILD_TMPDIR must be an absolute path')
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = tempfile.TemporaryDirectory(prefix='build-', dir=root)
    except OSError as error:
        raise BuildSafetyError(f'Cannot create build workspace in {root}: {error}') from error
    with temporary as directory:
        workspace = Path(directory).resolve()
        probe = workspace / 'exec-probe'
        try:
            probe.write_text('#!/bin/sh\nexit 0\n')
            probe.chmod(0o700)
            subprocess.run([str(probe)], check=True, capture_output=True, env=environment)
        except (OSError, subprocess.CalledProcessError) as error:
            raise BuildSafetyError(
                f'Build temporary directory {root} does not permit execution. '
                'Set CEDAR_BUILD_TMPDIR to an executable, writable filesystem; '
                'the system /tmp mount need not change.') from error
        finally:
            probe.unlink(missing_ok=True)
        scratch = workspace / 'tmp'
        scratch.mkdir(mode=0o700)
        environment.update({key: str(scratch) for key in ('TMPDIR', 'TMP', 'TEMP')})
        if java:
            # JAVA_TOOL_OPTIONS reaches Maven and every forked test JVM. Keep all
            # other options, but reject higher-precedence overrides of our path.
            for key in ('_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'MAVEN_OPTS'):
                if '-Djava.io.tmpdir=' in environment.get(key, ''):
                    raise BuildSafetyError(
                        f'{key} overrides java.io.tmpdir; use CEDAR_BUILD_TMPDIR instead')
            if any(char in str(scratch) for char in ('"', '\n', '\r')):
                raise BuildSafetyError('Build temporary path cannot contain quotes or newlines')
            option = f'"-Djava.io.tmpdir={scratch}"'
            environment['JAVA_TOOL_OPTIONS'] = (
                environment.get('JAVA_TOOL_OPTIONS', '') + ' ' + option).strip()
        yield workspace, environment


@dataclasses.dataclass(frozen=True)
class EmbeddedMongoProcess:
    pid: int
    executable: Path
    listeners: tuple[str, ...] = ()

    def describe(self) -> str:
        endpoints = f"; listening on {', '.join(self.listeners)}" if self.listeners else ""
        return f"PID {self.pid} ({self.executable}{endpoints})"


def _is_embedded_mongod(path: Path) -> bool:
    return path.name == "mongod" and ".embedmongo" in path.parts


def _parse_lsof_embedded_mongods(output: str) -> dict[int, Path]:
    processes = {}
    pid = None
    for line in output.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            executable = Path(line[1:])
            if _is_embedded_mongod(executable):
                processes[pid] = executable
    return processes


def _listening_endpoints(pid: int, command_runner=subprocess.run) -> tuple[str, ...]:
    try:
        result = command_runner(
            ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
            check=False, text=True, capture_output=True,
            env=invocation_environment(),
        )
    except OSError:
        return ()
    return tuple(sorted({
        line[1:] for line in (result.stdout or "").splitlines()
        if line.startswith("n")
    }))


def embedded_mongo_processes(
    *, command_runner=subprocess.run, proc_root: Path = Path("/proc"),
) -> list[EmbeddedMongoProcess]:
    """Return only Flapdoodle mongods, never the workstation's native MongoDB."""
    found = {}
    if proc_root.is_dir():
        for candidate in proc_root.iterdir():
            if not candidate.name.isdigit():
                continue
            try:
                executable = (candidate / "exe").resolve(strict=True)
            except OSError:
                continue
            if _is_embedded_mongod(executable):
                found[int(candidate.name)] = executable
    else:
        try:
            result = command_runner(
                ["lsof", "-nP", "-c", "mongod", "-a", "-d", "txt", "-Fpcn"],
                check=False, text=True, capture_output=True,
                env=invocation_environment(),
            )
        except OSError as error:
            raise BuildSafetyError(
                f"cannot inspect embedded Mongo test processes: {error}") from error
        if result.returncode not in {0, 1}:
            detail = (result.stderr or "").strip()
            raise BuildSafetyError(
                "cannot inspect embedded Mongo test processes"
                + (f": {detail}" if detail else ""))
        found = _parse_lsof_embedded_mongods(result.stdout or "")
    return [
        EmbeddedMongoProcess(pid, executable, _listening_endpoints(pid, command_runner))
        for pid, executable in sorted(found.items())
    ]


def _embedded_mongo_failure(context: str, processes: list[EmbeddedMongoProcess]) -> BuildSafetyError:
    detail = ", ".join(process.describe() for process in processes)
    return BuildSafetyError(
        f"refusing {context}: embedded Mongo test process(es) remain: {detail}. "
        "End the owning test, or run `cedarcli test cleanup`."
    )


def require_no_embedded_mongo_processes(context: str) -> None:
    processes = embedded_mongo_processes()
    if processes:
        raise _embedded_mongo_failure(context, processes)


def wait_for_no_embedded_mongo_processes(
    context: str, *, timeout_seconds: float = 5.0, poll_seconds: float = 0.1,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        processes = embedded_mongo_processes()
        if not processes:
            return
        if time.monotonic() >= deadline:
            raise _embedded_mongo_failure(context, processes)
        time.sleep(poll_seconds)


def is_test_bearing_maven_command(command: str) -> bool:
    try:
        arguments = shlex.split(command)
    except ValueError:
        arguments = command.split()
    executable = arguments[0] if arguments else ""
    is_maven = executable in {"mvn", "./mvnw"} or executable.endswith("/mvnw")
    skips_tests = any(argument in {
        "-DskipTests", "-DskipTests=true", "-Dmaven.test.skip=true",
    } for argument in arguments[1:])
    return is_maven and not skips_tests


def is_maven_command(command: str) -> bool:
    try:
        executable = shlex.split(command)[0]
    except (ValueError, IndexError):
        return False
    return Path(executable).name in {'mvn', 'mvnw'}


def tracked_state(root: Path) -> bytes:
    """Capture tracked worktree and index state, including pre-existing changes."""
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=no"],
        check=True, capture_output=True,
        env=invocation_environment(),
    ).stdout
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--binary", "HEAD", "--"],
        check=True, capture_output=True,
        env=invocation_environment(),
    ).stdout
    return status + b"\0" + diff


def repository_root(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=False, text=True, capture_output=True,
        env=invocation_environment(),
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def capture_estate_state(cedar_home: Path) -> dict[Path, bytes]:
    result = {}
    for candidate in sorted(cedar_home.iterdir()):
        if not candidate.is_dir() or not (candidate / ".git").exists():
            continue
        result[candidate.resolve()] = tracked_state(candidate)
    return result


def changed_repositories(before: dict[Path, bytes], after: dict[Path, bytes]) -> list[Path]:
    return sorted(root for root in set(before) | set(after) if before.get(root) != after.get(root))


def _process_cwd(pid: int) -> Path | None:
    proc_cwd = Path("/proc") / str(pid) / "cwd"
    if proc_cwd.exists():
        try:
            return proc_cwd.resolve()
        except OSError:
            return None
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            check=False, text=True, capture_output=True,
            env=invocation_environment(),
        )
    except OSError:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n/"):
            return Path(line[1:]).resolve()
    return None


def frontend_runtime_collisions(source: Path) -> list[tuple[int, str]]:
    """Return active dev runtimes whose cwd is inside the source checkout."""
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="], check=True, text=True, capture_output=True,
            env=invocation_environment(),
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    source = source.resolve()
    collisions = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, command = int(parts[0]), parts[1]
        normalized = " ".join(command.lower().split())
        if not any(marker in normalized for marker in FRONTEND_RUNTIME_MARKERS):
            continue
        cwd = _process_cwd(pid)
        if cwd is not None and (cwd == source or source in cwd.parents):
            collisions.append((pid, command))
    return collisions


@contextlib.contextmanager
def isolated_frontend_workspace(source: Path):
    """Build a checkout copy with private dependencies, npm cache, and Angular cache."""
    source = source.resolve()
    before_root = repository_root(source)
    before = tracked_state(before_root) if before_root is not None else None
    collisions = frontend_runtime_collisions(source)
    try:
        with executable_build_workspace() as (temporary_root, environment):
            build_root = temporary_root / source.name

            def ignore(_directory, names):
                return {name for name in names if name in {".git", "node_modules", ".angular"}}

            shutil.copytree(source, build_root, symlinks=True, ignore=ignore)
            existing_path = environment.get("PATH", "")
            local_binaries = str(build_root / "node_modules" / ".bin")
            environment.update({
                "CI": "true",
                "NG_CLI_ANALYTICS": "false",
                "npm_config_cache": str(temporary_root / "npm-cache"),
                # npm ci creates this directory during the first command. Resolve the following
                # bare ng/gulp/vite command from the isolated dependency graph, even on a clean
                # runner that has no globally installed frontend CLI.
                "PATH": local_binaries + (os.pathsep + existing_path if existing_path else ""),
            })
            yield build_root, environment, collisions
    finally:
        if before_root is not None and tracked_state(before_root) != before:
            raise BuildSafetyError(
                f"frontend build changed tracked source state in {before_root}; "
                "the pre-build state was preserved as the comparison baseline"
            )


def require_no_frontend_runtime_collision(source: Path) -> None:
    collisions = frontend_runtime_collisions(source)
    if not collisions:
        return
    detail = ", ".join(f"PID {pid} ({command})" for pid, command in collisions)
    raise BuildSafetyError(
        f"refusing an in-place frontend build while a runtime uses {source}: {detail}"
    )

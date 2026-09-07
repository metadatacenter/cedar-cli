from __future__ import annotations

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


def tracked_state(root: Path) -> bytes:
    """Capture tracked worktree and index state, including pre-existing changes."""
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=no"],
        check=True, capture_output=True,
    ).stdout
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--binary", "HEAD", "--"],
        check=True, capture_output=True,
    ).stdout
    return status + b"\0" + diff


def repository_root(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=False, text=True, capture_output=True,
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
def isolated_frontend_workspace(source: Path, *, reuse_node_modules: bool = False):
    """Build a checkout copy with private dependencies, npm cache, and Angular cache."""
    source = source.resolve()
    before_root = repository_root(source)
    before = tracked_state(before_root) if before_root is not None else None
    collisions = frontend_runtime_collisions(source)
    try:
        with tempfile.TemporaryDirectory(prefix=f"cedarcli-build-{source.name}-") as temporary:
            temporary_root = Path(temporary)
            build_root = temporary_root / source.name

            def ignore(_directory, names):
                return {name for name in names if name in {".git", "node_modules", ".angular"}}

            shutil.copytree(source, build_root, symlinks=True, ignore=ignore)
            if reuse_node_modules:
                dependencies = source / "node_modules"
                if not dependencies.is_dir():
                    raise BuildSafetyError(
                        f"{source} requires its existing node_modules, but none is installed"
                    )
                (build_root / "node_modules").symlink_to(dependencies, target_is_directory=True)
            environment = dict(os.environ)
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

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


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
            environment.update({
                "CI": "true",
                "NG_CLI_ANALYTICS": "false",
                "npm_config_cache": str(temporary_root / "npm-cache"),
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

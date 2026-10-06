"""Notice an npm install that silently dropped an optional dependency.

When an optional package fails to install, npm discards the error, logs one verbose line, and
leaves the package on disk until the lifecycle scripts have run. It deletes it afterwards. The
platform binaries that esbuild and rolldown ship are optional packages, and a fresh build cache
downloads them every time. When a download breaks mid-transfer, the outcome depends on the
package. esbuild's postinstall executes the truncated binary and fails with EBADMACHO, reported
as "Unknown system error -88". rolldown has no install script, so the install succeeds and the
build fails later with "Cannot find native binding".

The console shows neither cause. npm's debug log names the dropped package but not the error that
dropped it, so a caller can still tell this case apart and install again.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

_DROPPED = re.compile(r"^\d+ verbose reify failed optional dependency (.+)$")


def is_install(command: str) -> bool:
    """`npm ci` or `npm install`, with or without options such as `--prefix visual`."""
    tokens = command.split()
    return tokens[:1] == ["npm"] and "run" not in tokens and bool({"ci", "install"} & set(tokens))


def log_directory(cache) -> Path | None:
    """Where npm writes its debug logs when `npm_config_cache` is `cache`."""
    return Path(cache) / "_logs" if cache else None


def debug_logs(cache) -> set[Path]:
    directory = log_directory(cache)
    if directory is None or directory.is_symlink() or not directory.is_dir():
        return set()
    return {path for path in directory.glob("*-debug-*.log")
            if path.is_file() and not path.is_symlink()}


def dropped_optional_dependencies(cache, before: set[Path]) -> list[str]:
    """The packages that npm logs written since `before` report as dropped, by package name."""
    return _dropped_in(sorted(debug_logs(cache) - before))


def _dropped_in(logs) -> list[str]:
    dropped = set()
    for log in logs:
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = _DROPPED.match(line)
            if match:
                dropped.add(match[1].rsplit("node_modules/", 1)[-1])
    return sorted(dropped)


def dropped_message(dropped: list[str], again: bool) -> str:
    """What to tell whoever is watching an install npm dropped `dropped` from."""
    noun = "dependency" if len(dropped) == 1 else "dependencies"
    what = f"the optional {noun} {', '.join(dropped)}"
    if again:
        return f"npm dropped {what} again, so the install fails rather than leave the build without it."
    return f"npm dropped {what}, most likely after a failed download, so the install runs again."


def install(run, command: list[str], note, failure=RuntimeError) -> None:
    """Run an npm install, once more if npm dropped an optional dependency, and fail if it drops one again.

    The build executor reads the debug logs in the cache an isolated build gives npm. An install
    elsewhere shares the user's cache with whatever else npm is doing, so each attempt here writes
    its debug log to a directory of its own and the check reads that log alone.

    `run` runs one attempt and raises if it fails. A failure with nothing dropped is raised as it
    was, since installing again would not help it. `note` is told of each drop, and `failure` is
    the exception raised when the second attempt drops one too.
    """
    error = None
    dropped: list[str] = []
    for again in (False, True):
        with tempfile.TemporaryDirectory(prefix="npm-logs-") as logs:
            error = None
            try:
                run([*command, f"--logs-dir={logs}"])
            except Exception as raised:  # each caller's runner raises its own type
                error = raised
            dropped = _dropped_in(sorted(Path(logs).glob("*-debug-*.log")))
        if not dropped:
            if error is not None:
                raise error
            return
        note(dropped_message(dropped, again))
    raise failure(dropped_message(dropped, True)) from error


def main(argv: list[str]) -> int:
    """`python3 npm_install.py npm ci ...`: the install, held to the same rule, for a shell script."""
    command = argv[1:]
    if not command or command[0] != "npm" or not is_install(" ".join(command)):
        print("usage: npm_install.py npm ci|install [options]", file=sys.stderr)
        return 2
    try:
        install(lambda attempt: subprocess.run(attempt, check=True), command,
                lambda message: print(message, file=sys.stderr, flush=True))
    except subprocess.CalledProcessError as error:
        return error.returncode or 1
    except RuntimeError as error:
        print(error, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

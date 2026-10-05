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
    dropped = set()
    for log in sorted(debug_logs(cache) - before):
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = _DROPPED.match(line)
            if match:
                dropped.add(match[1].rsplit("node_modules/", 1)[-1])
    return sorted(dropped)

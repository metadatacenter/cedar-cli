"""CEDAR release hashes."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import hashlib
import json
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _directory_file_hashes(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise ReleaseError(f"build output directory is missing: {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReleaseError(f"build output contains a symbolic link: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = _file_sha256(path)
    if not files:
        raise ReleaseError(f"build output directory is empty: {root}")
    return files


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ReleaseError(f"required release input is missing: {path}")
    return _sha256(path.read_bytes())

"""Provision the release's Node from nodejs.org when no installed Node is that version.

A package manager offers the newest patch of a Node line, and the release needs one patch
exactly, so the version the CLI pins can disappear from the host the day the package manager
moves on. The CLI then fetches the archive nodejs.org publishes for that version, checks it
against the checksum this repository pins, and keeps it under ``$CEDAR_HOME/.cedar/toolchains``
where every later command finds it without downloading again.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from org.metadatacenter.release_support.policy import (
    NODE_DIST_URL,
    REQUIRED_NODE_ARCHIVE_SHA256,
    REQUIRED_NODE_VERSION,
)

Fetch = Callable[[str, Path], None]

_SYSTEMS = {"darwin": "darwin", "linux": "linux"}
_MACHINES = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}


class NodeProvisionError(Exception):
    """The release's Node could not be provisioned; the message says why."""


def node_platform(system: str | None = None, machine: str | None = None) -> str | None:
    """The platform part of a nodejs.org archive name, such as ``darwin-arm64``."""
    os_name = _SYSTEMS.get((system or platform.system()).lower())
    arch = _MACHINES.get((machine or platform.machine()).lower())
    return f"{os_name}-{arch}" if os_name and arch else None


def toolchain_root(cedar_home: str | Path) -> Path:
    return Path(cedar_home) / ".cedar" / "toolchains"


def _download_archive(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out)


def _archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def provision_node(cedar_home: str | Path, *, system: str | None = None,
                   machine: str | None = None, fetch: Fetch | None = None) -> Path:
    """The ``bin`` directory of the release's Node, downloading and unpacking it when absent.

    The archive is checked before anything is unpacked, and it is unpacked beside its final
    place and moved there in one step, so a failed or concurrent download never leaves a partial
    Node where a later command would take it.
    """
    version = REQUIRED_NODE_VERSION
    plat = node_platform(system, machine)
    expected = REQUIRED_NODE_ARCHIVE_SHA256.get(plat) if plat else None
    if expected is None:
        host = f"{system or platform.system()} {machine or platform.machine()}"
        raise NodeProvisionError(f"the CLI pins no Node {version} archive for {host}")
    root = toolchain_root(cedar_home)
    name = f"node-{version}-{plat}"
    target = root / name
    if (target / "bin" / "node").is_file():
        return target / "bin"
    url = f"{NODE_DIST_URL}/{version}/{name}.tar.xz"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix=".provision-") as work:
        archive = Path(work) / f"{name}.tar.xz"
        try:
            (fetch or _download_archive)(url, archive)
        except OSError as error:
            raise NodeProvisionError(f"{url} could not be downloaded: {error}") from error
        digest = _archive_sha256(archive)
        if digest != expected:
            raise NodeProvisionError(
                f"{url} has SHA-256 {digest}, not the {expected} the CLI pins; nothing was unpacked")
        try:
            with tarfile.open(archive, "r:xz") as tar:
                tar.extractall(work, filter="data")
        except (tarfile.TarError, OSError) as error:
            raise NodeProvisionError(f"{url} could not be unpacked: {error}") from error
        unpacked = Path(work) / name
        if not (unpacked / "bin" / "node").is_file():
            raise NodeProvisionError(f"{url} holds no {name}/bin/node")
        try:
            os.replace(unpacked, target)
        except OSError:
            # Another command provisioned the same Node first; theirs is as good as this one.
            if not (target / "bin" / "node").is_file():
                raise
    return target / "bin"

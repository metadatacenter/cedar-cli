"""Extracting a tar archive so that nothing in it can land outside its destination."""
from __future__ import annotations

import posixpath
import tarfile
from pathlib import Path

# tarfile gained `extractall(filter=...)` in 3.12, backported to 3.10.12 and 3.11.4. The macOS
# runner image resolves "3.10" to 3.10.11, where the keyword is a TypeError, so the refusals that
# matter for the archives the CLI unpacks are made by hand there.
HAS_DATA_FILTER = hasattr(tarfile, "data_filter")


class UnsafeArchive(tarfile.TarError):
    """An archive member that would land outside the destination, or is not an ordinary file."""


def extract_within(content: tarfile.TarFile, destination: Path | str) -> None:
    """Extract every member under `destination`, refusing any member or link that leaves it."""
    if HAS_DATA_FILTER:
        content.extractall(destination, filter="data")
        return
    # A member or link target that leaves the destination, and anything that is not a regular
    # file, directory or link.
    for member in content.getmembers():
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise UnsafeArchive(f"holds a special file: {member.name}")
        targets = [member.name]
        if member.issym():
            targets.append(posixpath.join(posixpath.dirname(member.name), member.linkname))
        elif member.islnk():
            targets.append(member.linkname)
        for target in targets:
            resolved = posixpath.normpath(target)
            if posixpath.isabs(target) or resolved == ".." or resolved.startswith("../"):
                raise UnsafeArchive(f"leaves its destination: {member.name}")
    content.extractall(destination)

"""Pack and inspect npm artifacts without depending on a build or release workflow."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

from org.metadatacenter.util.ProcessRunner import run_process
from org.metadatacenter.util.SubprocessDiagnostics import describe_subprocess_failure


class NpmPackageError(RuntimeError):
    """An npm package could not be packed or inspected."""


@dataclass(frozen=True)
class PackageInspection:
    package: dict
    sha256: str
    integrity: str


def inspect_tarball(content: bytes, identity: str) -> PackageInspection:
    """Read the unique regular manifest and hash the exact bytes the caller supplies.

    Call again after any workflow-specific tarball transformation so evidence describes
    the artifact actually stored or published. Nothing is extracted onto the filesystem.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            members = [member for member in archive.getmembers()
                       if PurePosixPath(member.name) == PurePosixPath("package/package.json")]
            if len(members) != 1 or not members[0].isfile():
                raise NpmPackageError(f"{identity} has no unique regular package/package.json")
            stream = archive.extractfile(members[0])
            if stream is None:
                raise NpmPackageError(f"{identity} package.json is unreadable")
            package = json.load(stream)
    except (OSError, EOFError, tarfile.TarError, ValueError) as error:
        raise NpmPackageError(f"{identity} is not a readable npm tarball: {error}") from error
    if not isinstance(package, dict):
        raise NpmPackageError(f"{identity} package.json is not an object")
    return PackageInspection(
        package=package,
        sha256=hashlib.sha256(content).hexdigest(),
        integrity="sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode(),
    )


def pack_and_inspect(source: Path, destination: Path, *, environment=None, cache=None):
    """Pack built output into a private destination with no existing tarballs.

    npm owns file selection, but lifecycle scripts must not rebuild the supplied output.
    Callers own temporary directories, cache policy, storage and publication.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    try:
        package = json.loads((source / "package.json").read_bytes())
        if (not isinstance(package, dict) or any(
                not isinstance(package.get(key), str) or not package[key]
                for key in ("name", "version"))):
            raise NpmPackageError(f"{source} must identify a named, versioned npm package")
        destination.mkdir(parents=True, exist_ok=True)
        if list(destination.glob("*.tgz")):
            raise NpmPackageError(f"{destination} already contains an npm tarball")
        command = ["npm", "pack", "--ignore-scripts", "--json",
                   "--pack-destination", str(destination)]
        if cache is not None:
            command.extend(["--cache", str(Path(cache).resolve())])
        result = run_process(command, cwd=str(source), env=environment)
        if result.returncode:
            detail = "\n".join(result) or "the process produced no diagnostic output of its own"
            raise NpmPackageError(
                f"npm pack {describe_subprocess_failure(result.returncode)} for {source}: {detail}")
        tarballs = list(destination.glob("*.tgz"))
        if len(tarballs) != 1 or tarballs[0].is_symlink() or not tarballs[0].is_file():
            raise NpmPackageError("npm pack did not produce exactly one regular tarball")
        tarball = tarballs[0]
        inspection = inspect_tarball(tarball.read_bytes(), str(source))
        if any(inspection.package.get(key) != package[key] for key in ("name", "version")):
            raise NpmPackageError(f"{source}: packed package identity differs from build output")
        return tarball, inspection
    except (OSError, ValueError) as error:
        raise NpmPackageError(f"Cannot pack {source}: {error}") from error

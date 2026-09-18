"""Whether each published npm surface can be packed the way the publisher packs it."""
from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
import tempfile

from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.policy import NPM_RELEASE_SURFACES

PACKAGING_REMEDY = (
    "pack the surface from a clean archive of its commit, or move what the pack needs into a "
    "prepared build the publisher overlays"
)


def _archive(repository: Path, package_path: str, destination: Path, runner) -> None:
    """Lay down the committed tree the publisher would pack, and nothing else.

    The publisher packs `git archive HEAD`, deliberately without node_modules or any ignored
    build output, so that packing cannot carry a working copy's state into a released package.
    """
    archive = runner(
        ["git", "-C", str(repository), "archive", "--format=tar", "HEAD"],
        capture_output=True, check=False,
    )
    if archive.returncode != 0:
        raise ReleaseError(f"cannot archive {repository.name}: HEAD is unreadable")
    extract = runner(
        ["tar", "-xf", "-", "-C", str(destination)],
        input=archive.stdout, capture_output=True, check=False,
    )
    if extract.returncode != 0:
        raise ReleaseError(f"cannot extract the archive of {repository.name}")


def _reason(result) -> str:
    """The line that says what went wrong, rather than the first line that is not a notice.

    A failing pack prints the script banner, then a stack frame naming a file in the throwaway
    staging directory, then the message. Reporting the first line gives the operator a path
    that no longer exists; the thrown message is what identifies the defect.
    """
    lines = [line.strip() for line in
             ((result.stderr or "") + "\n" + (result.stdout or "")).splitlines()
             if line.strip() and not line.strip().startswith("npm notice")]
    for marker in ("Error:", "npm error code", "error TS", "ERR!"):
        for line in lines:
            if marker in line:
                return line[:200]
    return lines[0][:200] if lines else f"npm pack exited {result.returncode}"


def packaging_findings(workspace, surfaces=None, runner=subprocess.run) -> list[str]:
    """Every published npm surface that cannot be packed from its own committed tree.

    A pack runs the package's `prepack` script, so a surface whose prepack reads node_modules,
    or any other path the archive does not carry, cannot be published at all. Nothing says so
    until the train reaches its final npm stage, twenty minutes after it started, and the
    recovery is a new train rather than a resume.

    The pack is a dry run: it reports what would be packed and writes no tarball.
    """
    root = Path(workspace)
    findings = []
    for surface in (surfaces if surfaces is not None else NPM_RELEASE_SURFACES):
        repository = root / surface["repository"]
        if not (repository / ".git").exists():
            continue
        package_path = surface.get("directory", ".")
        staging = Path(tempfile.mkdtemp(prefix="cedar-pack-preflight."))
        try:
            source = staging / "source"
            source.mkdir()
            _archive(repository, package_path, source, runner)
            packed = source if package_path == "." else source / package_path
            if not (packed / "package.json").exists():
                findings.append(
                    f"{surface['id']}: the committed tree has no {package_path}/package.json")
                continue
            result = runner(
                ["npm", "pack", "--dry-run", "--ignore-scripts=false", "--loglevel=error"],
                cwd=str(packed), capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                findings.append(f"{surface['id']}: {_reason(result)}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return findings


def packaging_preflight(workspace, surfaces=None, runner=subprocess.run) -> None:
    """Refuse when a published surface cannot be packed from its committed tree."""
    findings = packaging_findings(workspace, surfaces, runner)
    if findings:
        raise ReleaseError(
            "published npm surfaces cannot be packed from their committed trees: "
            + "; ".join(findings) + f"; {PACKAGING_REMEDY}")

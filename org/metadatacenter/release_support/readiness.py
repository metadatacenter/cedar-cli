"""Release preconditions answerable from the workspace, before a train exists.

A release consumes a completed train, so `release plan` cannot run until one has been built.
That order is backwards for anything the plan checks that a train has no bearing on: a
consumer inventory that disagrees with itself, a published surface that cannot be packed, a
required artifact no module produces. Each of those is settled by reading `develop`, and each
of them, discovered at plan time, costs a whole train to repair — the train is spent the moment
the fix is committed, because a release stamps the exact commits its train captured.

These checks therefore run against the workspace alone, so the answer arrives before anything
has been paid for. What remains for `release plan` is what genuinely needs the built train:
the tarball inventories, the registry digests, and the byte proof between the train's CEE and
the public package.
"""
from __future__ import annotations
from pathlib import Path
import json
import re

from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.packaging import packaging_findings
from org.metadatacenter.release_support.policy import NPM_RELEASE_SURFACES

SNAPSHOT_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)-SNAPSHOT$")
RELEASE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _configuration(workspace: Path) -> tuple[dict, dict]:
    ops = workspace / "cedar-development" / "ops"
    try:
        frontend = json.loads((ops / "frontend-train.json").read_text(encoding="utf-8"))
        build = json.loads((ops / "build-train.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(f"cannot read the train configuration: {error}") from error
    return frontend, build


def cee_consumer_findings(workspace: Path, frontend: dict) -> list[str]:
    """Whether the CEE consumer inventory is coherent and present on disk.

    The release pins the public editor into every declared consumer, so an inventory that
    names a manifest which is not there fails in the middle of a release rather than before
    one, and a duplicate entry pins the same manifest twice.
    """
    declared = []
    for host in frontend.get("frontends", []) or []:
        consumer = host.get("ceeConsumer")
        if isinstance(consumer, dict):
            declared.append((host.get("repository"), consumer.get("manifest"), consumer.get("lock")))
    for extra in frontend.get("additionalCeeConsumers", []) or []:
        declared.append((extra.get("repository"), extra.get("manifest"), extra.get("lock")))

    findings = []
    if not declared:
        findings.append("the configuration declares no CEE consumers")
    seen = set()
    for repository, manifest, lock in declared:
        if not repository or not manifest or not lock:
            findings.append(f"a CEE consumer entry is incomplete: {repository or '?'}")
            continue
        if (repository, manifest) in seen:
            findings.append(f"{repository}:{manifest} is declared as a CEE consumer twice")
        seen.add((repository, manifest))
        root = workspace / repository
        if not (root / ".git").exists():
            continue
        for relative in (manifest, lock):
            if not (root / relative).exists():
                findings.append(f"{repository} declares {relative}, which is not in the checkout")
    return findings


def required_artifact_findings(workspace: Path, build: dict) -> list[str]:
    """Whether every required Maven artifact is produced by a module in the workspace.

    The publication verifies its inventory against this list, so a name that no module builds
    fails after the artifacts have been uploaded rather than before the release starts.
    """
    produced = set()
    for pom in workspace.glob("*/pom.xml"):
        produced.add(pom.parent.name)
        try:
            text = pom.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        produced.update(re.findall(r"<module>([^<]+)</module>", text))
    produced = {Path(name).name for name in produced}
    return [
        f"{artifact} is required but no module in the workspace builds it"
        for artifact in build.get("requiredArtifacts", []) or []
        if artifact not in produced
    ]


def surface_findings(workspace: Path) -> list[str]:
    """Whether every published npm surface is present with the manifest the release packs."""
    findings = []
    for surface in NPM_RELEASE_SURFACES:
        root = workspace / surface["repository"]
        if not (root / ".git").exists():
            findings.append(f"{surface['id']}: {surface['repository']} is not checked out")
            continue
        directory = surface.get("directory", ".")
        manifest = root / directory / "package.json" if directory != "." else root / "package.json"
        if not manifest.exists():
            findings.append(f"{surface['id']}: {directory}/package.json is missing")
    return findings


def version_findings(release_version: str | None, next_version: str | None,
                     workspace: Path) -> list[str]:
    """Whether the requested versions are well formed and follow the workspace's own."""
    findings = []
    if release_version and not RELEASE_RE.match(release_version):
        findings.append(f"the release version {release_version} is not a three-part version")
    if next_version and not SNAPSHOT_RE.match(next_version):
        findings.append(f"the next version {next_version} is not a SNAPSHOT")
    if not (release_version and next_version):
        return findings
    if not (RELEASE_RE.match(release_version) and SNAPSHOT_RE.match(next_version)):
        return findings
    current = release_version.split(".")
    following = next_version.removesuffix("-SNAPSHOT").split(".")
    if [int(part) for part in following] <= [int(part) for part in current]:
        findings.append(
            f"the next version {next_version} does not follow the release {release_version}")
    parent = workspace / "cedar-parent" / "pom.xml"
    if parent.exists():
        text = parent.read_text(encoding="utf-8", errors="ignore")
        stamped = re.search(r"<version>(\d+\.\d+\.\d+)-SNAPSHOT</version>", text)
        if stamped and stamped.group(1) != release_version:
            findings.append(
                f"the workspace is on {stamped.group(1)}-SNAPSHOT, so it releases "
                f"{stamped.group(1)} rather than {release_version}")
    return findings


def readiness_findings(workspace, release_version=None, next_version=None,
                       packaging=True) -> list[str]:
    """Every release precondition that a train has no bearing on."""
    root = Path(workspace)
    frontend, build = _configuration(root)
    findings = []
    findings.extend(cee_consumer_findings(root, frontend))
    findings.extend(required_artifact_findings(root, build))
    findings.extend(surface_findings(root))
    findings.extend(version_findings(release_version, next_version, root))
    if packaging:
        findings.extend(
            f"a published surface cannot be packed from its committed tree: {item}"
            for item in packaging_findings(root))
    return findings

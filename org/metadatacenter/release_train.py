"""Manifest-backed CEDAR releases sourced from immutable build trains.

This module owns the whole release. It plans one from explicit inputs, settles every
precondition before the first build, and drives the phases that follow through to
published artifacts and a recorded proof that the release holds.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import typer
from rich.console import Console

from org.metadatacenter.util.BuildTrain import BuildTrain


app = typer.Typer()
console = Console()

PUBLIC_NPM_REGISTRY = "https://registry.npmjs.org/"
DEV_CEE_NAME = "@org.metadatacenter/cedar-embeddable-editor"
PUBLIC_CEE_NAME = "cedar-embeddable-editor"
STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
NEXT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+-SNAPSHOT$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEV_MODEL_SPEC_RE = re.compile(
    rb"npm:@org\.metadatacenter/cedar-model-typescript-library@"
    rb"[0-9]+\.[0-9]+\.[0-9]+-dev\.[0-9A-Za-z.-]+"
)
LOAD_TRACE_RE = re.compile(
    rb"20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}"
    rb"(?: [0-9a-f]{7,40})?"
)
REQUIRED_CEE_FILES = {
    "bundle-manifest.json",
    "cedar-embeddable-editor.d.ts",
    "cedar-embeddable-editor.js",
    "package.json",
}
INDEPENDENT_RELEASE_REPOSITORIES = {
    "cedar-embeddable-editor",
    "cedar-model-typescript-library",
    "cedar-template-designer",
    "cedar-workspace",
}
NPM_VERSION_SURFACES = {
    "cedar-template-editor": ["."],
    "cedar-openview": ["cedar-openview-src", "cedar-openview-dist"],
    "cedar-content-distribution": ["."],
    "cedar-monitoring": ["cedar-monitoring-src", "cedar-monitoring-dist"],
    "cedar-bridging": ["cedar-bridging-src", "cedar-bridging-dist"],
    "cedar-component-demo": [
        "cedar-cee-demo-angular-src",
        "cedar-cee-demo-angular-dist",
        "cedar-cee-demo-ember-src",
    ],
}
LICENSE_FILE_NAME = "license.txt"
LICENSE_COPYRIGHT_RE = re.compile(r"^Copyright \(c\) (\d{4}),", re.MULTILINE)

MAVEN_GENERATED_VERSION_FILES = {
    "cedar-resource-server": {
        "cedar-resource-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-resource-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-terminology-server": {
        "cedar-terminology-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-terminology-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
    "cedar-valuerecommender-server": {
        "cedar-valuerecommender-server-application/src/main/resources/assets/swagger-api/swagger.json": (
            '"version" : "{}"'
        ),
        "cedar-valuerecommender-server-application/src/main/resources/assets/swagger-api/swagger.yaml": (
            "version: {}"
        ),
    },
}
FRONTEND_BUILD_SURFACES = [
    {"id": "template-editor", "repository": "cedar-template-editor", "directory": ".",
     "install": [], "build": []},
    {"id": "workspace", "repository": "cedar-workspace", "directory": ".",
     "install": [], "build": []},
    {"id": "openview", "repository": "cedar-openview", "directory": "cedar-openview-src",
     "install": ["--legacy-peer-deps"], "build": ["npm", "run", "build"],
     "buildOutput": "cedar-openview-src/dist/cedar-openview"},
    {"id": "bridging", "repository": "cedar-bridging", "directory": "cedar-bridging-src",
     "install": [], "build": ["npm", "run", "build"],
     "buildOutput": "cedar-bridging-src/dist/cedar-bridging"},
    {"id": "monitoring", "repository": "cedar-monitoring", "directory": "cedar-monitoring-src",
     "install": ["--legacy-peer-deps"], "build": ["npm", "run", "build"],
     "buildOutput": "cedar-monitoring-src/dist/cedar-monitoring"},
    {"id": "content", "repository": "cedar-content-distribution", "directory": ".",
     "install": ["--legacy-peer-deps"], "build": []},
    {"id": "cee-demo-angular", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-angular-src", "install": ["--legacy-peer-deps"],
     "build": ["npm", "run", "build"],
     "buildOutput": "cedar-cee-demo-angular-src/dist/cedar-cee-demo-angular-src"},
    {"id": "cee-demo-ember", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-ember-src", "install": [],
     "build": ["npm", "run", "build"]},
    {"id": "cee-demo-react", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-react", "install": [],
     "build": ["npm", "run", "build"]},
]
MAVEN_RELEASE_REPOSITORY = "https://nexus.bmir.stanford.edu/repository/releases/"
MAVEN_SNAPSHOT_REPOSITORY = "https://nexus.bmir.stanford.edu/repository/snapshots/"
NPM_RELEASE_SURFACES = [
    {"id": "template-editor", "repository": "cedar-template-editor", "directory": "."},
    {
        "id": "openview", "repository": "cedar-openview", "directory": "cedar-openview-dist",
        "buildOutput": "cedar-openview-src/dist/cedar-openview",
    },
    {"id": "content", "repository": "cedar-content-distribution", "directory": "."},
    {
        "id": "monitoring", "repository": "cedar-monitoring",
        "directory": "cedar-monitoring-dist",
        "buildOutput": "cedar-monitoring-src/dist/cedar-monitoring",
    },
    {
        "id": "bridging", "repository": "cedar-bridging", "directory": "cedar-bridging-dist",
        "buildOutput": "cedar-bridging-src/dist/cedar-bridging",
    },
    {
        "id": "cee-demo-angular", "repository": "cedar-component-demo",
        "directory": "cedar-cee-demo-angular-dist",
        "buildOutput": "cedar-cee-demo-angular-src/dist/cedar-cee-demo-angular-src",
    },
]


class ReleaseError(RuntimeError):
    """A release input or immutable artifact failed validation."""


class RetryableReleaseError(ReleaseError):
    """A release step failed for a reason that may not hold a moment later.

    An unattended release must survive a network fault without surviving a guard, so the
    two are different exceptions rather than the same one read for its wording. Only the
    transport raises this: a refusal carrying an HTTP status, a changed tree or a failed
    verification is never retryable, however transient it looks.
    """


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


def _validate_stable_version(value: str, label: str) -> str:
    if not STABLE_VERSION_RE.fullmatch(value or ""):
        raise ReleaseError(f"invalid {label} {value!r}; expected MAJOR.MINOR.PATCH")
    return value


def _stable_version_key(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))


class HttpClient:
    """Small authenticated reader used for state and npm registry artifacts."""

    def __init__(self, opener=None, environment=None):
        self.opener = opener or urllib.request.urlopen
        self.environment = os.environ if environment is None else environment

    def _headers(self, url: str) -> dict[str, str]:
        if not url.startswith("https://nexus.bmir.stanford.edu/"):
            return {}
        username = self.environment.get("BMIR_NEXUS_USERNAME")
        password = self.environment.get("BMIR_NEXUS_PASSWORD")
        if not username or not password:
            return {}
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def read(self, url: str, *, missing_ok: bool = False) -> bytes | None:
        request = urllib.request.Request(url, headers=self._headers(url))
        try:
            with self.opener(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise ReleaseError(f"cannot read {url}: HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RetryableReleaseError(f"cannot read {url}: {error}") from error

    def read_json(self, url: str, *, missing_ok: bool = False) -> tuple[dict, bytes] | None:
        content = self.read(url, missing_ok=missing_ok)
        if content is None:
            return None
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            raise ReleaseError(f"invalid JSON at {url}: {error}") from error
        if not isinstance(value, dict):
            raise ReleaseError(f"expected a JSON object at {url}")
        return value, content


class TrainState:
    def __init__(self, http: HttpClient, base_url: str = BuildTrain.STATE_BASE_URL):
        self.http = http
        self.base_url = base_url.rstrip("/")

    def read_json(self, relative_path: str) -> tuple[dict, bytes]:
        result = self.http.read_json(f"{self.base_url}/{relative_path}")
        assert result is not None
        return result


def _verify_integrity(identity: str, content: bytes, integrity: str) -> None:
    try:
        algorithm, encoded = integrity.split("-", 1)
    except ValueError as error:
        raise ReleaseError(f"{identity} has invalid registry integrity {integrity!r}") from error
    if algorithm not in hashlib.algorithms_available:
        raise ReleaseError(f"{identity} uses unsupported integrity algorithm {algorithm}")
    actual = base64.b64encode(hashlib.new(algorithm, content).digest()).decode()
    if actual != encoded:
        raise ReleaseError(f"{identity} tarball does not match registry integrity")


def _tarball_files(identity: str, content: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ReleaseError(f"{identity} contains unsafe archive path {member.name!r}")
                if member.isdir():
                    continue
                if not member.isfile() or len(path.parts) < 2 or path.parts[0] != "package":
                    raise ReleaseError(f"{identity} contains unexpected archive member {member.name!r}")
                relative = PurePosixPath(*path.parts[1:]).as_posix()
                if relative in files:
                    raise ReleaseError(f"{identity} contains duplicate file {relative!r}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ReleaseError(f"{identity} cannot read {relative!r}")
                files[relative] = stream.read()
    except tarfile.TarError as error:
        raise ReleaseError(f"{identity} is not a readable npm tarball") from error
    missing = REQUIRED_CEE_FILES - files.keys()
    if missing:
        raise ReleaseError(f"{identity} is missing required files: {', '.join(sorted(missing))}")
    return files


def _read_package_json(identity: str, files: dict[str, bytes], name: str) -> dict:
    try:
        value = json.loads(files[name])
    except (KeyError, json.JSONDecodeError) as error:
        raise ReleaseError(f"{identity} has no readable {name}") from error
    if not isinstance(value, dict):
        raise ReleaseError(f"{identity} {name} is not a JSON object")
    return value


def _normalize_package_metadata(
    identity: str,
    files: dict[str, bytes],
    *,
    expected_name: str,
    expected_version: str,
    development: bool,
) -> dict[str, bytes]:
    result = copy.deepcopy(files)
    package = _read_package_json(identity, files, "package.json")
    if package.get("name") != expected_name or package.get("version") != expected_version:
        raise ReleaseError(
            f"{identity} package.json identifies {package.get('name')}@{package.get('version')}"
        )
    publish_config = package.get("publishConfig")
    if development:
        expected_publish_config = {
            "registry": "https://nexus.bmir.stanford.edu/repository/npm-cedar/",
            "tag": "dev",
        }
        if publish_config != expected_publish_config:
            raise ReleaseError(f"{identity} has unexpected Nexus publishConfig")
    elif publish_config is not None:
        raise ReleaseError(f"{identity} public package must not contain publishConfig")
    package["name"] = "<cee-package-name>"
    package["version"] = "<cee-package-version>"
    package.pop("publishConfig", None)
    result["package.json"] = _json_bytes(package)

    if "package-lock.json" in files:
        lock = _read_package_json(identity, files, "package-lock.json")
        if lock.get("name") != expected_name or lock.get("version") != expected_version:
            raise ReleaseError(f"{identity} package-lock.json has unexpected root identity")
        root = lock.get("packages", {}).get("")
        if not isinstance(root, dict):
            raise ReleaseError(f"{identity} package-lock.json has no root package")
        if root.get("name") != expected_name or root.get("version") != expected_version:
            raise ReleaseError(f"{identity} package-lock.json root has unexpected identity")
        lock["name"] = "<cee-package-name>"
        lock["version"] = "<cee-package-version>"
        root["name"] = "<cee-package-name>"
        root["version"] = "<cee-package-version>"
        result["package-lock.json"] = _json_bytes(lock)
    return result


def _verify_bundle(identity: str, files: dict[str, bytes]) -> None:
    manifest = _read_package_json(identity, files, "bundle-manifest.json")
    bundle = files["cedar-embeddable-editor.js"]
    if manifest.get("bytes") != len(bundle) or manifest.get("sha256") != _sha256(bundle):
        raise ReleaseError(f"{identity} JavaScript does not match bundle-manifest.json")


def _tree_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(files[name]).to_bytes(8, "big"))
        digest.update(files[name])
    return digest.hexdigest()


def _public_release_changelog(
    identity: str,
    changelog: bytes,
    public_version: str,
) -> tuple[bytes, str]:
    """Remove one current-release entry and return its declared public model version."""

    try:
        text = changelog.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseError(f"{identity} CHANGELOG.md is not UTF-8") from error
    heading = re.compile(
        rf"^## \[{re.escape(public_version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\n"
        rf".*?(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    entries = list(heading.finditer(text))
    if len(entries) != 1:
        raise ReleaseError(
            f"{identity} must contain exactly one dated CHANGELOG.md entry for "
            f"{public_version}"
        )
    entry = entries[0]
    model_versions = set(re.findall(
        r"cedar-model-typescript-library@(\d+\.\d+\.\d+)", entry.group(0)
    ))
    if len(model_versions) != 1:
        raise ReleaseError(
            f"{identity} {public_version} changelog entry must declare exactly one "
            "public cedar-model-typescript-library version"
        )
    without_entry = text[:entry.start()] + text[entry.end():]
    return without_entry.encode("utf-8"), model_versions.pop()


def _one_match(identity: str, label: str, pattern: re.Pattern[bytes], content: bytes) -> bytes:
    matches = pattern.findall(content)
    if len(matches) != 1:
        raise ReleaseError(f"{identity} bundle must contain exactly one {label}; found {len(matches)}")
    return matches[0]


def _replace_once(identity: str, label: str, content: bytes, old: bytes, new: bytes) -> bytes:
    count = content.count(old)
    if count != 1:
        raise ReleaseError(f"{identity} bundle must contain exactly one {label}; found {count}")
    return content.replace(old, new, 1)


def _normalize_bundle_provenance(
    dev_identity: str,
    dev_bundle: bytes,
    dev_version: str,
    public_identity: str,
    public_bundle: bytes,
    public_version: str,
    public_model_version: str,
) -> tuple[bytes, bytes]:
    """Normalize only the three provenance literals deliberately changed for npmjs."""

    normalized_dev = dev_bundle
    normalized_public = public_bundle
    substitutions = (
        (
            "CEE version",
            dev_version.encode(),
            public_version.encode(),
            b"<cee-version>",
        ),
        (
            "model package identity",
            _one_match(dev_identity, "development model package identity", DEV_MODEL_SPEC_RE,
                       dev_bundle),
            public_model_version.encode(),
            b"<model-package-identity>",
        ),
        (
            "load trace",
            _one_match(dev_identity, "load trace", LOAD_TRACE_RE, dev_bundle),
            _one_match(public_identity, "load trace", LOAD_TRACE_RE, public_bundle),
            b"<cee-load-trace>",
        ),
    )
    for label, dev_value, public_value, placeholder in substitutions:
        normalized_dev = _replace_once(
            dev_identity, label, normalized_dev, dev_value, placeholder,
        )
        normalized_public = _replace_once(
            public_identity, label, normalized_public, public_value, placeholder,
        )
    return normalized_dev, normalized_public


def _normalized_bundle_manifest(bundle: bytes) -> bytes:
    return _json_bytes({"bytes": len(bundle), "sha256": _sha256(bundle)})


def compare_cee_packages(
    dev_tarball: bytes,
    dev_version: str,
    public_tarball: bytes,
    public_version: str,
) -> dict:
    """Prove that a public CEE package is a metadata-only promotion of a train package."""

    dev_identity = f"{DEV_CEE_NAME}@{dev_version}"
    public_identity = f"{PUBLIC_CEE_NAME}@{public_version}"
    dev_files = _tarball_files(dev_identity, dev_tarball)
    public_files = _tarball_files(public_identity, public_tarball)
    _verify_bundle(dev_identity, dev_files)
    _verify_bundle(public_identity, public_files)
    normalized_dev = _normalize_package_metadata(
        dev_identity,
        dev_files,
        expected_name=DEV_CEE_NAME,
        expected_version=dev_version,
        development=True,
    )
    normalized_public = _normalize_package_metadata(
        public_identity,
        public_files,
        expected_name=PUBLIC_CEE_NAME,
        expected_version=public_version,
        development=False,
    )
    if normalized_dev.keys() != normalized_public.keys():
        only_dev = sorted(normalized_dev.keys() - normalized_public.keys())
        only_public = sorted(normalized_public.keys() - normalized_dev.keys())
        raise ReleaseError(
            "CEE promotion changes the package file set: "
            f"development-only={only_dev}, public-only={only_public}"
        )
    changed = [
        name for name in sorted(normalized_dev)
        if normalized_dev[name] != normalized_public[name]
    ]
    if changed:
        allowed = {
            "CHANGELOG.md", "bundle-manifest.json", "cedar-embeddable-editor.js",
        }
        unexpected = sorted(set(changed) - allowed)
        if unexpected:
            raise ReleaseError(
                "CEE promotion changes package content outside allowed channel metadata: "
                + ", ".join(unexpected)
            )
        changed_set = set(changed)
        bundle_changed = "cedar-embeddable-editor.js" in changed_set
        manifest_changed = "bundle-manifest.json" in changed_set
        if bundle_changed != manifest_changed:
            raise ReleaseError(
                "CEE promotion changes an incomplete bundle-provenance pair: "
                + ", ".join(changed)
            )

        public_model_version = None
        if "CHANGELOG.md" in changed_set:
            normalized_changelog, public_model_version = _public_release_changelog(
                public_identity, public_files["CHANGELOG.md"], public_version,
            )
            if normalized_changelog != dev_files["CHANGELOG.md"]:
                raise ReleaseError(
                    "CEE promotion changes CHANGELOG.md outside the one current-release entry"
                )
            normalized_dev["CHANGELOG.md"] = normalized_changelog
            normalized_public["CHANGELOG.md"] = normalized_changelog
        elif bundle_changed:
            # The train may have captured develop after the public release entry was merged. In
            # that case the changelogs are already byte-identical, but the entry still declares
            # which public model identity replaces the scoped train identity in the bundle.
            _, public_model_version = _public_release_changelog(
                public_identity, public_files["CHANGELOG.md"], public_version,
            )

        if bundle_changed:
            assert public_model_version is not None
            dev_bundle, public_bundle = _normalize_bundle_provenance(
                dev_identity,
                dev_files["cedar-embeddable-editor.js"],
                dev_version,
                public_identity,
                public_files["cedar-embeddable-editor.js"],
                public_version,
                public_model_version,
            )
            if dev_bundle != public_bundle:
                raise ReleaseError(
                    "CEE promotion changes executable JavaScript outside declared release provenance"
                )
            normalized_dev["cedar-embeddable-editor.js"] = dev_bundle
            normalized_public["cedar-embeddable-editor.js"] = public_bundle
            normalized_dev["bundle-manifest.json"] = _normalized_bundle_manifest(dev_bundle)
            normalized_public["bundle-manifest.json"] = _normalized_bundle_manifest(public_bundle)
        changed = [
            name for name in sorted(normalized_dev)
            if normalized_dev[name] != normalized_public[name]
        ]
    if changed:
        raise ReleaseError(
            "CEE promotion changes package content outside allowed channel metadata: "
            + ", ".join(changed)
        )
    digest = _tree_digest(normalized_dev)
    return {
        "algorithm": "sha256",
        "normalizedPayloadSha256": digest,
        "fileCount": len(normalized_dev),
        "bundleSha256": _sha256(dev_files["cedar-embeddable-editor.js"]),
        "publicBundleSha256": _sha256(public_files["cedar-embeddable-editor.js"]),
        "normalizedBundleSha256": _sha256(normalized_dev["cedar-embeddable-editor.js"]),
        "allowedMetadataChanges": [
            "package.json:name",
            "package.json:version",
            "package.json:publishConfig",
            "package-lock.json:name",
            "package-lock.json:version",
            "package-lock.json:packages['']:name",
            "package-lock.json:packages['']:version",
            "cedar-embeddable-editor.js:CEE version",
            "cedar-embeddable-editor.js:model package identity",
            "cedar-embeddable-editor.js:load trace",
            "bundle-manifest.json:derived bundle bytes and sha256",
            f"CHANGELOG.md:{public_version} release entry",
        ],
    }


class ReleasePlanner:
    def __init__(self, http: HttpClient | None = None, state: TrainState | None = None):
        self.http = http or HttpClient()
        self.state = state or TrainState(self.http)

    def _public_record(self, version: str) -> dict:
        package_url = (
            PUBLIC_NPM_REGISTRY.rstrip("/") + "/"
            + urllib.parse.quote(PUBLIC_CEE_NAME, safe="")
        )
        result = self.http.read_json(package_url)
        assert result is not None
        metadata, _ = result
        record = metadata.get("versions", {}).get(version)
        if not isinstance(record, dict):
            raise ReleaseError(f"npmjs does not contain {PUBLIC_CEE_NAME}@{version}")
        return record

    def _development_config(self, source: dict, filename: str) -> tuple[dict, str, str]:
        revision = source.get("repositories", {}).get("cedar-development")
        if not isinstance(revision, str) or not GIT_SHA_RE.fullmatch(revision):
            raise ReleaseError("train source manifest has no cedar-development revision")
        url = (
            "https://raw.githubusercontent.com/metadatacenter/cedar-development/"
            f"{revision}/ops/{filename}"
        )
        result = self.http.read_json(url)
        assert result is not None
        config, content = result
        return config, url, _sha256(content)

    @staticmethod
    def _release_repositories(build_config: dict, source: dict) -> tuple[list[str], list[str]]:
        configured = build_config.get("repositories")
        maven = build_config.get("mavenRepositories")
        if not isinstance(configured, list) or not configured:
            raise ReleaseError("train build configuration has no repositories")
        if not isinstance(maven, list) or not maven:
            raise ReleaseError("train build configuration has no Maven repositories")
        source_repositories = source.get("repositories", {})
        if set(configured) != set(source_repositories):
            missing = sorted(set(configured) - set(source_repositories))
            extra = sorted(set(source_repositories) - set(configured))
            raise ReleaseError(
                "train source and build configuration repository sets differ: "
                f"missing={missing}, extra={extra}"
            )
        if not set(maven).issubset(configured):
            raise ReleaseError("train Maven repository set is not part of the source repository set")
        release = [
            repository for repository in configured
            if repository not in INDEPENDENT_RELEASE_REPOSITORIES
        ]
        return release, maven

    @staticmethod
    def _maven_phases(build_config: dict, maven_repositories: list[str]) -> list[dict]:
        phases = build_config.get("phases")
        if not isinstance(phases, list) or not phases:
            raise ReleaseError("train build configuration has no Maven phases")
        result = []
        for phase in phases:
            if not isinstance(phase, dict):
                raise ReleaseError("train build configuration contains an invalid Maven phase")
            name = phase.get("name")
            repository = phase.get("repository")
            if not isinstance(name, str) or not name or repository not in maven_repositories:
                raise ReleaseError(f"invalid train Maven phase {phase!r}")
            result.append({"name": name, "repository": repository})
        return result

    @staticmethod
    def _publication_plan(build_config: dict, release_repositories: list[str]) -> dict:
        required = build_config.get("requiredArtifacts")
        if not isinstance(required, list) or not required or not all(
            isinstance(item, str) and item for item in required
        ):
            raise ReleaseError("train build configuration has no required Maven artifacts")
        npm_surfaces = copy.deepcopy(NPM_RELEASE_SURFACES)
        missing = sorted(
            {surface["repository"] for surface in npm_surfaces} - set(release_repositories)
        )
        if missing:
            raise ReleaseError(
                "release repository set is missing npm publication repositories: "
                + ", ".join(missing)
            )
        return {
            "maven": {
                "releaseRepository": MAVEN_RELEASE_REPOSITORY,
                "nextDevelopmentRepository": MAVEN_SNAPSHOT_REPOSITORY,
                "requiredArtifacts": list(required),
            },
            "npm": {
                "registry": "https://nexus.bmir.stanford.edu/repository/npm-cedar/",
                "surfaces": npm_surfaces,
            },
        }

    @staticmethod
    def _cee_consumers(config: dict, npm_plan: dict, source: dict) -> list[dict]:
        repositories = source.get("repositories", {})
        if not isinstance(repositories, dict):
            raise ReleaseError("train source manifest has no repository inventory")
        planned_frontends = {
            item.get("repository"): item
            for item in npm_plan.get("frontends", [])
            if item.get("ceeVersion") is not None
        }
        planned_additional = {
            (item.get("repository"), item.get("manifest")): item
            for item in npm_plan.get("additionalCeeConsumers", [])
        }
        consumers = []
        for frontend in config.get("frontends", []):
            consumer = frontend.get("ceeConsumer")
            if not isinstance(consumer, dict):
                continue
            repository = frontend.get("repository")
            planned = planned_frontends.pop(repository, None)
            if not isinstance(planned, dict):
                raise ReleaseError(f"npm plan has no CEE-wired frontend record for {repository}")
            revision = repositories.get(repository)
            if planned.get("revision") != revision:
                raise ReleaseError(f"npm plan and source manifest disagree for {repository}")
            consumers.append({
                "label": frontend.get("id", repository),
                "repository": repository,
                "revision": revision,
                "manifest": consumer.get("manifest"),
                "lock": consumer.get("lock"),
            })
        for consumer in config.get("additionalCeeConsumers", []):
            repository = consumer.get("repository")
            manifest = consumer.get("manifest")
            planned = planned_additional.pop((repository, manifest), None)
            if not isinstance(planned, dict):
                raise ReleaseError(
                    f"npm plan has no additional CEE consumer record for {repository}/{manifest}"
                )
            revision = repositories.get(repository)
            if planned.get("revision") != revision:
                raise ReleaseError(f"npm plan and source manifest disagree for {repository}")
            consumers.append({
                "label": manifest,
                "repository": repository,
                "revision": revision,
                "manifest": manifest,
                "lock": consumer.get("lock"),
            })
        if planned_frontends or planned_additional:
            raise ReleaseError("frontend configuration does not cover every CEE consumer in npm plan")
        for consumer in consumers:
            if not GIT_SHA_RE.fullmatch(consumer.get("revision") or ""):
                raise ReleaseError(
                    f"train source manifest has no valid revision for {consumer.get('repository')}"
                )
            for field in ("manifest", "lock"):
                value = consumer.get(field)
                if not isinstance(value, str) or not value or PurePosixPath(value).is_absolute() \
                        or ".." in PurePosixPath(value).parts:
                    raise ReleaseError(
                        f"CEE consumer {consumer.get('repository')} has invalid {field} path"
                    )
        if len(consumers) != 7:
            raise ReleaseError(f"expected 7 CEE consumers, found {len(consumers)}")
        return consumers

    @staticmethod
    def _distribution(identity: str, record: dict) -> tuple[str, str]:
        distribution = record.get("dist", {})
        tarball_url = distribution.get("tarball")
        integrity = distribution.get("integrity")
        if not isinstance(tarball_url, str) or not isinstance(integrity, str):
            raise ReleaseError(f"{identity} has no tarball and integrity metadata")
        return tarball_url, integrity

    def build(
        self,
        *,
        release_version: str,
        next_version: str,
        train: str,
        cee_version: str,
    ) -> dict:
        _validate_stable_version(release_version, "release version")
        _validate_stable_version(cee_version, "CEE version")
        if not NEXT_VERSION_RE.fullmatch(next_version or ""):
            raise ReleaseError(
                f"invalid next development version {next_version!r}; expected MAJOR.MINOR.PATCH-SNAPSHOT"
            )
        next_stable = next_version.removesuffix("-SNAPSHOT")
        if _stable_version_key(next_stable) <= _stable_version_key(release_version):
            raise ReleaseError(
                f"next development version {next_version} must be newer than release {release_version}"
            )
        try:
            BuildTrain.validate(train)
        except ValueError as error:
            raise ReleaseError(str(error)) from error
        if train.split("-dev.", 1)[0] != release_version:
            raise ReleaseError(
                f"train {train} is not a development train for explicit release {release_version}"
            )

        source, source_content = self.state.read_json(f"trains/{train}.json")
        source_version = source.get("sourceVersion")
        if source_version != f"{release_version}-SNAPSHOT":
            raise ReleaseError(
                f"train source version is {source_version!r}, expected {release_version}-SNAPSHOT"
            )
        completion, _ = self.state.read_json(f"completed/{train}.json")
        npm_plan, npm_plan_content = self.state.read_json(f"npm/trains/{train}.json")
        npm_completion, _ = self.state.read_json(f"npm/completed/{train}.json")
        for label, value in (
            ("source manifest", source),
            ("completion record", completion),
            ("npm plan", npm_plan),
            ("npm completion", npm_completion),
        ):
            if value.get("version") != train:
                raise ReleaseError(f"{label} does not describe train {train}")
        source_sha256 = _sha256(source_content)
        if npm_plan.get("sourceManifestSha256") != source_sha256:
            raise ReleaseError("npm plan does not match the train source manifest")
        if npm_completion.get("sourceManifestSha256") != source_sha256:
            raise ReleaseError("npm completion does not match the train source manifest")
        npm_plan_sha256 = _sha256(npm_plan_content)
        if npm_completion.get("planSha256") != npm_plan_sha256:
            raise ReleaseError("npm completion does not match the npm plan")
        frontend_config, frontend_config_url, frontend_config_sha256 = self._development_config(
            source, "frontend-train.json"
        )
        build_config, build_config_url, build_config_sha256 = self._development_config(
            source, "build-train.json"
        )
        release_repositories, maven_repositories = self._release_repositories(
            build_config, source
        )
        maven_phases = self._maven_phases(build_config, maven_repositories)
        publication_plan = self._publication_plan(build_config, release_repositories)
        consumers = self._cee_consumers(frontend_config, npm_plan, source)

        planned_cee = npm_plan.get("cee", {})
        dev_version = planned_cee.get("version")
        if planned_cee.get("name") != DEV_CEE_NAME or not isinstance(dev_version, str):
            raise ReleaseError(f"npm plan has no {DEV_CEE_NAME} development package")
        dev_record = next(
            (
                package for package in npm_completion.get("packages", [])
                if package.get("name") == DEV_CEE_NAME and package.get("version") == dev_version
            ),
            None,
        )
        if not isinstance(dev_record, dict):
            raise ReleaseError(f"npm completion has no verified {DEV_CEE_NAME}@{dev_version}")
        dev_url = dev_record.get("tarball")
        dev_integrity = dev_record.get("integrity")
        dev_tarball_sha256 = dev_record.get("tarballSha256")
        if not isinstance(dev_url, str) or not isinstance(dev_integrity, str):
            raise ReleaseError("npm completion CEE record has no tarball and integrity")
        if not isinstance(dev_tarball_sha256, str) or not SHA256_RE.fullmatch(dev_tarball_sha256):
            raise ReleaseError("npm completion CEE record has no valid tarball SHA-256")
        dev_tarball = self.http.read(dev_url)
        assert dev_tarball is not None
        _verify_integrity(f"{DEV_CEE_NAME}@{dev_version}", dev_tarball, dev_integrity)
        if _sha256(dev_tarball) != dev_tarball_sha256:
            raise ReleaseError("train CEE tarball does not match its recorded SHA-256")

        public_record = self._public_record(cee_version)
        public_url, public_integrity = self._distribution(
            f"{PUBLIC_CEE_NAME}@{cee_version}", public_record
        )
        public_tarball = self.http.read(public_url)
        assert public_tarball is not None
        _verify_integrity(
            f"{PUBLIC_CEE_NAME}@{cee_version}", public_tarball, public_integrity
        )
        proof = compare_cee_packages(
            dev_tarball, dev_version, public_tarball, cee_version
        )

        return {
            "schemaVersion": 1,
            "releaseVersion": release_version,
            "nextDevelopmentVersion": next_version,
            "train": train,
            "sourceVersion": source_version,
            "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "phase": "validated",
            "trainState": {
                "sourceManifest": f"trains/{train}.json",
                "sourceManifestSha256": source_sha256,
                "npmPlan": f"npm/trains/{train}.json",
                "npmPlanSha256": npm_plan_sha256,
                "npmCompletion": f"npm/completed/{train}.json",
                "frontendConfig": frontend_config_url,
                "frontendConfigSha256": frontend_config_sha256,
                "buildConfig": build_config_url,
                "buildConfigSha256": build_config_sha256,
            },
            "sourceRepositories": source.get("repositories"),
            "releaseRepositories": release_repositories,
            "mavenRepositories": maven_repositories,
            "mavenPhases": maven_phases,
            "publicationPlan": publication_plan,
            "cee": {
                "development": {
                    "name": DEV_CEE_NAME,
                    "version": dev_version,
                    "integrity": dev_integrity,
                    "tarball": dev_url,
                    "tarballSha256": dev_tarball_sha256,
                    "revision": planned_cee.get("revision"),
                },
                "public": {
                    "name": PUBLIC_CEE_NAME,
                    "version": cee_version,
                    "integrity": public_integrity,
                    "tarball": public_url,
                    "tarballSha256": _sha256(public_tarball),
                    "gitHead": public_record.get("gitHead"),
                },
                "promotionProof": proof,
                "consumers": consumers,
            },
        }


class ReleaseState:
    def __init__(self, root: Path | None = None, environment=None):
        environment = os.environ if environment is None else environment
        configured = environment.get("CEDAR_RELEASE_STATE_DIR")
        self.root = root or (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".cedar" / "train-releases"
        )

    @property
    def current_path(self) -> Path:
        return self.root / "current.json"

    def manifest_path(self, release_version: str) -> Path:
        return self.root / "releases" / f"{release_version}.json"

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(_json_bytes(value))
        temporary.replace(path)

    def start(self, manifest: dict) -> Path:
        if self.current_path.exists():
            current = self.read_current()
            if not current.get("concludedAt"):
                raise ReleaseError(
                    f"release {current['releaseVersion']} is already active; "
                    "use cedarcli release status"
                )
        path = self.manifest_path(manifest["releaseVersion"])
        if path.exists():
            raise ReleaseError(f"release state already exists at {path}")
        active = copy.deepcopy(manifest)
        active["phase"] = "started"
        active["startedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self._write(path, active)
        self._write(self.current_path, {
            "schemaVersion": 1,
            "releaseVersion": active["releaseVersion"],
            "manifest": str(path),
            "startedAt": active["startedAt"],
        })
        return path

    def conclude(self) -> None:
        """Record that the active release has finished, so it no longer holds the slot.

        Nothing used to mark a release finished, so the pointer at current.json kept naming
        it forever and the next release could not start. The pointer stays where it is,
        stamped rather than deleted, so status still has the last release to show; what
        changes is that start no longer treats it as in progress.
        """
        current = self.read_current()
        current["concludedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self._write(self.current_path, current)

    def read_current(self) -> dict:
        if not self.current_path.exists():
            raise ReleaseError("there is no active train-backed release")
        try:
            current = json.loads(self.current_path.read_bytes())
        except json.JSONDecodeError as error:
            raise ReleaseError(f"invalid release state at {self.current_path}") from error
        return current

    def read_current_manifest(self) -> tuple[dict, Path]:
        current = self.read_current()
        path = Path(current.get("manifest", ""))
        if not path.is_file():
            raise ReleaseError(f"active release manifest is missing: {path}")
        try:
            manifest = json.loads(path.read_bytes())
        except json.JSONDecodeError as error:
            raise ReleaseError(f"invalid release manifest at {path}") from error
        if manifest.get("releaseVersion") != current.get("releaseVersion"):
            raise ReleaseError("active release pointer and manifest disagree")
        return manifest, path

    def update_current_manifest(self, changes: dict) -> tuple[dict, Path]:
        manifest, path = self.read_current_manifest()
        manifest.update(copy.deepcopy(changes))
        self._write(path, manifest)
        return manifest, path


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ReleaseError(f"required release input is missing: {path}")
    return _sha256(path.read_bytes())


class ReleaseWorkspacePreparer:
    """Prepare stable-CEE frontend inputs in clones of the train's exact commits."""

    def __init__(self, state: ReleaseState, command_runner=None, environment=None):
        self.state = state
        self.command_runner = command_runner or subprocess.run
        self.environment = dict(os.environ if environment is None else environment)

    def next_attempt(self, release_version: str) -> Path:
        attempts = self.state.root / "attempts" / release_version
        number = 1
        if attempts.is_dir():
            used = [
                int(path.name) for path in attempts.iterdir()
                if path.is_dir() and path.name.isdigit()
            ]
            if used:
                number = max(used) + 1
        return attempts / f"{number:03d}"

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        environment=None,
        stream: bool = False,
    ) -> str:
        try:
            result = self.command_runner(
                args,
                cwd=str(cwd) if cwd else None,
                env=environment,
                text=True,
                capture_output=not stream,
                check=False,
            )
        except OSError as error:
            raise ReleaseError(f"cannot run {args[0]}: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise ReleaseError(f"command failed ({' '.join(args)}){suffix}")
        return (result.stdout or "").strip()

    def _clone(self, repository: str, revision: str, destination: Path) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", repository):
            raise ReleaseError(f"invalid train repository name {repository!r}")
        if not GIT_SHA_RE.fullmatch(revision or ""):
            raise ReleaseError(f"invalid train revision for {repository}: {revision!r}")
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        source = Path(cedar_home) / repository
        if not (source / ".git").exists():
            raise ReleaseError(f"local source repository is missing: {source}")
        self._run(["git", "-C", str(source), "cat-file", "-e", f"{revision}^{{commit}}"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run([
            "git", "clone", "--quiet", "--no-checkout", "--local",
            str(source), str(destination),
        ])
        self._run(["git", "-C", str(destination), "checkout", "--quiet", "--detach", revision])
        actual = self._run(["git", "-C", str(destination), "rev-parse", "HEAD"])
        if actual != revision:
            raise ReleaseError(f"isolated clone for {repository} is {actual}, expected {revision}")

    @staticmethod
    def _consumer_paths(workspace: Path, consumer: dict) -> tuple[Path, Path]:
        root = workspace / consumer["repository"]
        return root / consumer["manifest"], root / consumer["lock"]

    @staticmethod
    def _verify_consumer(workspace: Path, consumer: dict, public_cee: dict) -> dict:
        manifest_path, lock_path = ReleaseWorkspacePreparer._consumer_paths(workspace, consumer)
        try:
            package = json.loads(manifest_path.read_bytes())
            lock = json.loads(lock_path.read_bytes())
        except json.JSONDecodeError as error:
            raise ReleaseError(f"CEE consumer has invalid JSON: {error}") from error
        dependency = "cedar-embeddable-editor"
        cee_version = public_cee["version"]
        declared = package.get("dependencies", {}).get(dependency)
        locked = lock.get("packages", {}).get("", {}).get("dependencies", {}).get(dependency)
        installed = lock.get("packages", {}).get(f"node_modules/{dependency}", {})
        if declared != cee_version:
            raise ReleaseError(f"{manifest_path} pins {declared!r}, expected {cee_version}")
        if locked != cee_version:
            raise ReleaseError(f"{lock_path} root pins {locked!r}, expected {cee_version}")
        if installed.get("version") != cee_version:
            raise ReleaseError(f"{lock_path} installs the wrong CEE version")
        if installed.get("resolved") != public_cee["tarball"]:
            raise ReleaseError(f"{lock_path} does not resolve the proven public CEE tarball")
        if installed.get("integrity") != public_cee["integrity"]:
            raise ReleaseError(f"{lock_path} does not carry the proven public CEE integrity")
        return {
            "label": consumer["label"],
            "repository": consumer["repository"],
            "manifest": consumer["manifest"],
            "lock": consumer["lock"],
            "manifestSha256": _file_sha256(manifest_path),
            "lockSha256": _file_sha256(lock_path),
            "integrity": installed["integrity"],
            "resolved": installed["resolved"],
        }

    def prepare(self, manifest: dict, attempt: Path) -> dict:
        consumers = manifest.get("cee", {}).get("consumers", [])
        if not isinstance(consumers, list) or len(consumers) != 7:
            raise ReleaseError("release manifest does not contain all seven CEE consumers")
        repositories = manifest.get("sourceRepositories", {})
        if not isinstance(repositories, dict):
            raise ReleaseError("release manifest has no source repository inventory")
        workspace = attempt / "workspace"
        required_repositories = sorted(
            {consumer["repository"] for consumer in consumers} | {"cedar-development"}
        )
        for repository in required_repositories:
            revision = repositories.get(repository)
            self._clone(repository, revision, workspace / repository)

        before = {}
        for consumer in consumers:
            manifest_path, lock_path = self._consumer_paths(workspace, consumer)
            before[(consumer["repository"], consumer["manifest"])] = {
                "manifestSha256": _file_sha256(manifest_path),
                "lockSha256": _file_sha256(lock_path),
            }

        public_cee = manifest["cee"]["public"]
        cee_version = public_cee["version"]
        helper = workspace / "cedar-development" / "ops" / "propagate-cee-release.mjs"
        if not helper.is_file():
            raise ReleaseError(f"train release helper is missing: {helper}")
        command_environment = dict(self.environment)
        command_environment["CEDAR_HOME"] = str(workspace)
        command_environment["npm_config_cache"] = str(attempt / "npm-cache")
        self._run(
            ["node", str(helper), "--apply", cee_version],
            cwd=workspace / "cedar-development",
            environment=command_environment,
            stream=True,
        )
        self._run(
            ["node", str(helper), "--check", cee_version],
            cwd=workspace / "cedar-development",
            environment=command_environment,
            stream=True,
        )

        verified = []
        allowed_by_repo: dict[str, set[str]] = {}
        for consumer in consumers:
            record = self._verify_consumer(workspace, consumer, public_cee)
            record["before"] = before[(consumer["repository"], consumer["manifest"])]
            verified.append(record)
            allowed_by_repo.setdefault(consumer["repository"], set()).update({
                consumer["manifest"], consumer["lock"],
            })
        changes = {}
        for repository in required_repositories:
            root = workspace / repository
            changed = set(filter(None, self._run([
                "git", "-C", str(root), "diff", "--name-only", "HEAD", "--",
            ]).splitlines()))
            untracked = set(filter(None, self._run([
                "git", "-C", str(root), "ls-files", "--others", "--exclude-standard",
            ]).splitlines()))
            actual = changed | untracked
            allowed = allowed_by_repo.get(repository, set())
            unexpected = sorted(actual - allowed)
            if unexpected:
                raise ReleaseError(
                    f"CEE propagation changed unexpected files in {repository}: "
                    + ", ".join(unexpected)
                )
            if repository != "cedar-development" and actual != allowed:
                missing = sorted(allowed - actual)
                raise ReleaseError(
                    f"CEE propagation did not update expected files in {repository}: "
                    + ", ".join(missing)
                )
            changes[repository] = sorted(actual)
        return {
            "attempt": attempt.name,
            "workspace": str(workspace),
            "preparedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "repositories": {
                repository: {
                    "revision": repositories[repository],
                    "path": str(workspace / repository),
                    "changedFiles": changes[repository],
                }
                for repository in required_repositories
            },
            "consumers": verified,
        }


class ReleaseVersionPreparer:
    """Create release and next-development source variants from the same train commits."""

    def __init__(self, state: ReleaseState, workspace_preparer=None):
        self.state = state
        self.workspace_preparer = workspace_preparer or ReleaseWorkspacePreparer(state)

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def _stamp_npm_surface(cls, root: Path, relative: str, old: str, new: str) -> set[str]:
        surface = root if relative == "." else root / relative
        manifest_path = surface / "package.json"
        lock_path = surface / "package-lock.json"
        try:
            package = json.loads(manifest_path.read_bytes())
            lock = json.loads(lock_path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise ReleaseError(f"cannot read npm version surface {surface}: {error}") from error
        if package.get("version") != old:
            raise ReleaseError(
                f"{manifest_path} version is {package.get('version')!r}, expected train source {old}"
            )
        if lock.get("version") != old:
            raise ReleaseError(
                f"{lock_path} version is {lock.get('version')!r}, expected train source {old}"
            )
        lock_root = lock.get("packages", {}).get("")
        if not isinstance(lock_root, dict) or lock_root.get("version") != old:
            raise ReleaseError(f"{lock_path} root package does not have train source version {old}")
        package["version"] = new
        lock["version"] = new
        lock_root["version"] = new
        cls._write_json(manifest_path, package)
        cls._write_json(lock_path, lock)
        prefix = "" if relative == "." else f"{relative}/"
        return {f"{prefix}package.json", f"{prefix}package-lock.json"}

    @staticmethod
    def _replace_exact(path: Path, old: bytes, new: bytes) -> bool:
        content = path.read_bytes()
        if old not in content:
            return False
        path.write_bytes(content.replace(old, new))
        return True

    @classmethod
    def _stamp_maven(cls, root: Path, old: str, new: str) -> set[str]:
        changed = set()
        for path in sorted(root.rglob("pom.xml")):
            if cls._replace_exact(path, old.encode(), new.encode()):
                changed.add(path.relative_to(root).as_posix())
        if not changed:
            raise ReleaseError(f"{root.name} has no Maven version {old} to stamp")
        for relative, pattern in MAVEN_GENERATED_VERSION_FILES.get(root.name, {}).items():
            path = root / relative
            old_marker = pattern.format(old).encode()
            new_marker = pattern.format(new).encode()
            try:
                content = path.read_bytes()
            except OSError as error:
                raise ReleaseError(f"cannot read tracked generated version file {path}: {error}") from error
            if content.count(old_marker) != 1:
                raise ReleaseError(
                    f"{path} does not contain exactly one generated API version {old}"
                )
            path.write_bytes(content.replace(old_marker, new_marker))
            changed.add(relative)
        return changed

    @classmethod
    def _stamp_development(cls, root: Path, old: str, new: str) -> set[str]:
        relative = "bin/util/set-env-generic.sh"
        path = root / relative
        changed = cls._replace_exact(
            path,
            f"export CEDAR_VERSION={old}".encode(),
            f"export CEDAR_VERSION={new}".encode(),
        )
        if not changed:
            raise ReleaseError(f"{path} does not declare train source version {old}")
        return {relative}

    @classmethod
    def _stamp_docker_build(cls, root: Path, old: str, new: str) -> set[str]:
        changed = set()
        for path in sorted(root.rglob("Dockerfile")):
            if cls._replace_exact(
                path,
                f"ENV CEDAR_VERSION={old}".encode(),
                f"ENV CEDAR_VERSION={new}".encode(),
            ):
                changed.add(path.relative_to(root).as_posix())
        base = root / "bin" / "cedar-images-base.sh"
        if cls._replace_exact(
            base,
            f"export IMAGE_VERSION={old}".encode(),
            f"export IMAGE_VERSION={new}".encode(),
        ):
            changed.add(base.relative_to(root).as_posix())
        if not changed:
            raise ReleaseError(f"{root.name} has no Docker version {old} to stamp")
        return changed

    @classmethod
    def _stamp_docker_deploy(cls, root: Path, old: str, new: str) -> set[str]:
        changed = set()
        for path in sorted(root.rglob(".env")):
            if cls._replace_exact(
                path,
                f"CEDAR_DOCKER_VERSION={old}".encode(),
                f"CEDAR_DOCKER_VERSION={new}".encode(),
            ):
                changed.add(path.relative_to(root).as_posix())
        if not changed:
            raise ReleaseError(f"{root.name} has no deployment version {old} to stamp")
        return changed

    @classmethod
    def _stamp_license(cls, root: Path, year: str) -> set[str]:
        """Move the copyright year forward so releases, rather than January, keep it current.

        Only the year moves, and only on a copyright line of the shape preflight recognised.
        A licence that does not carry one is left alone rather than rewritten to a guess.
        """
        path = root / LICENSE_FILE_NAME
        if not path.is_file():
            return set()
        text = path.read_text(encoding="utf-8")
        match = LICENSE_COPYRIGHT_RE.search(text)
        if match is None or match.group(1) == year:
            return set()
        path.write_text(text[:match.start(1)] + year + text[match.end(1):], encoding="utf-8")
        return {LICENSE_FILE_NAME}

    @classmethod
    def _stamp_repository(
        cls,
        repository: str,
        root: Path,
        old: str,
        new: str,
        maven_repositories: set[str],
        copyright_year: str,
    ) -> set[str]:
        changed = cls._stamp_license(root, copyright_year)
        return changed | cls._stamp_versions(repository, root, old, new, maven_repositories)

    @classmethod
    def _stamp_versions(
        cls,
        repository: str,
        root: Path,
        old: str,
        new: str,
        maven_repositories: set[str],
    ) -> set[str]:
        if repository in maven_repositories:
            return cls._stamp_maven(root, old, new)
        if repository in NPM_VERSION_SURFACES:
            changed = set()
            for relative in NPM_VERSION_SURFACES[repository]:
                changed.update(cls._stamp_npm_surface(root, relative, old, new))
            return changed
        if repository == "cedar-development":
            return cls._stamp_development(root, old, new)
        if repository == "cedar-docker-build":
            return cls._stamp_docker_build(root, old, new)
        if repository == "cedar-docker-deploy":
            return cls._stamp_docker_deploy(root, old, new)
        return set()

    def _ensure_clone(self, repository: str, revision: str, destination: Path) -> None:
        if (destination / ".git").exists():
            actual = self.workspace_preparer._run([
                "git", "-C", str(destination), "rev-parse", "HEAD",
            ])
            if actual != revision:
                raise ReleaseError(
                    f"existing isolated clone for {repository} is {actual}, expected {revision}"
                )
            return
        self.workspace_preparer._clone(repository, revision, destination)

    def _actual_changes(self, root: Path) -> set[str]:
        changed = set(filter(None, self.workspace_preparer._run([
            "git", "-C", str(root), "diff", "--name-only", "HEAD", "--",
        ]).splitlines()))
        untracked = set(filter(None, self.workspace_preparer._run([
            "git", "-C", str(root), "ls-files", "--others", "--exclude-standard",
        ]).splitlines()))
        return changed | untracked

    def prepare(self, manifest: dict) -> dict:
        frontend = manifest.get("frontendPreparation", {})
        release_workspace = Path(frontend.get("workspace", ""))
        if not release_workspace.is_dir():
            raise ReleaseError("release frontend workspace is missing")
        attempt = release_workspace.parent
        next_workspace = attempt / "next-workspace"
        release_repositories = manifest.get("releaseRepositories")
        repositories = manifest.get("sourceRepositories")
        maven_repositories = set(manifest.get("mavenRepositories", []))
        if not isinstance(release_repositories, list) or not release_repositories:
            raise ReleaseError("release manifest has no release repository set")
        if not isinstance(repositories, dict):
            raise ReleaseError("release manifest has no source repository inventory")
        old = manifest.get("sourceVersion")
        if not isinstance(old, str) or not NEXT_VERSION_RE.fullmatch(old):
            raise ReleaseError("release manifest has no explicit train source SNAPSHOT version")
        release_version = manifest["releaseVersion"]
        next_version = manifest["nextDevelopmentVersion"]
        copyright_year = str(dt.datetime.fromisoformat(manifest["createdAt"]).year)
        if old == next_version:
            raise ReleaseError("next development version must differ from the train source version")

        for repository in release_repositories:
            revision = repositories.get(repository)
            self._ensure_clone(repository, revision, release_workspace / repository)
            self._ensure_clone(repository, revision, next_workspace / repository)

        cee_allowed_by_repo: dict[str, set[str]] = {}
        for consumer in manifest["cee"]["consumers"]:
            cee_allowed_by_repo.setdefault(consumer["repository"], set()).update({
                consumer["manifest"], consumer["lock"],
            })

        for repository, relative_paths in cee_allowed_by_repo.items():
            if repository not in release_repositories:
                continue
            for relative in relative_paths:
                source = release_workspace / repository / relative
                destination = next_workspace / repository / relative
                expected = next(
                    record[f"{kind}Sha256"]
                    for record in frontend["consumers"]
                    if record["repository"] == repository
                    for kind in ("manifest", "lock")
                    if record[kind] == relative
                )
                if _file_sha256(source) != expected:
                    raise ReleaseError(f"prepared CEE consumer changed before version stamping: {source}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        variants = {}
        for variant, workspace, target in (
            ("release", release_workspace, release_version),
            ("nextDevelopment", next_workspace, next_version),
        ):
            records = {}
            for repository in release_repositories:
                root = workspace / repository
                stamped = self._stamp_repository(
                    repository, root, old, target, maven_repositories, copyright_year
                )
                allowed = set(stamped)
                allowed.update(cee_allowed_by_repo.get(repository, set()))
                actual = self._actual_changes(root)
                unexpected = sorted(actual - allowed)
                missing = sorted(allowed - actual)
                if unexpected or missing:
                    raise ReleaseError(
                        f"{variant} stamping produced an invalid change set for {repository}: "
                        f"unexpected={unexpected}, missing={missing}"
                    )
                records[repository] = {
                    "revision": repositories[repository],
                    "changedFiles": sorted(actual),
                    "fileSha256": {
                        relative: _file_sha256(root / relative)
                        for relative in sorted(actual)
                    },
                }
            variants[variant] = {
                "version": target,
                "workspace": str(workspace),
                "repositories": records,
            }
        return {
            "preparedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sourceVersion": old,
            **variants,
        }


class ReleaseBuildValidator:
    """Build prepared source variants without publishing or changing Git history."""

    def __init__(self, state: ReleaseState, executor=None, environment=None):
        self.state = state
        self.executor = executor
        self.environment = dict(os.environ if environment is None else environment)

    @staticmethod
    def _task_id(*parts: str) -> str:
        return ":".join(parts)

    def tasks(self, manifest: dict) -> list[dict]:
        versions = manifest.get("versionPreparation", {})
        phases = manifest.get("mavenPhases")
        if not isinstance(phases, list) or not phases:
            raise ReleaseError("release manifest has no ordered Maven build phases")
        tasks = []
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        for variant in ("release", "nextDevelopment"):
            variant_record = versions.get(variant, {})
            workspace = Path(variant_record.get("workspace", ""))
            if not workspace.is_dir():
                raise ReleaseError(f"prepared {variant} workspace is missing")
            local_repository = attempt / "build-cache" / variant / "m2" / "repository"
            for phase in phases:
                repository = phase["repository"]
                root = workspace / repository
                wrapper = root / "mvnw"
                if not wrapper.is_file():
                    raise ReleaseError(f"Maven wrapper is missing: {wrapper}")
                command = [
                    str(wrapper),
                    "--batch-mode",
                    "--no-transfer-progress",
                    f"-Dmaven.repo.local={local_repository}",
                    "clean",
                    "install",
                ]
                if variant == "nextDevelopment":
                    command.append("-DskipTests")
                tasks.append({
                    "id": self._task_id(variant, "maven", phase["name"]),
                    "variant": variant,
                    "kind": "maven",
                    "repository": repository,
                    "cwd": str(root),
                    "command": command,
                    "tests": variant == "release",
                })
            for surface in FRONTEND_BUILD_SURFACES:
                root = workspace / surface["repository"] / surface["directory"]
                if not root.is_dir():
                    if surface["repository"] not in manifest["releaseRepositories"]:
                        continue
                    raise ReleaseError(f"frontend build surface is missing: {root}")
                package = root / "package.json"
                lock = root / "package-lock.json"
                if not package.is_file() or not lock.is_file():
                    raise ReleaseError(f"frontend build surface has no package and lock: {root}")
                install = ["npm", "ci", *surface["install"]]
                tasks.append({
                    "id": self._task_id(variant, "npm", surface["id"], "install"),
                    "variant": variant,
                    "kind": "npm-install",
                    "repository": surface["repository"],
                    "cwd": str(root),
                    "command": install,
                    "tests": False,
                })
                if surface["build"]:
                    build_task = {
                        "id": self._task_id(variant, "npm", surface["id"], "build"),
                        "variant": variant,
                        "kind": "frontend-build",
                        "repository": surface["repository"],
                        "cwd": str(root),
                        "command": surface["build"],
                        "tests": False,
                    }
                    if surface.get("buildOutput"):
                        build_task["buildOutput"] = str(
                            workspace / surface["repository"] / surface["buildOutput"]
                        )
                    tasks.append(build_task)
        identifiers = [task["id"] for task in tasks]
        if len(identifiers) != len(set(identifiers)):
            raise ReleaseError("release build plan contains duplicate task identifiers")
        return tasks

    @staticmethod
    def _stream_command(command: list[str], cwd: Path, environment: dict, log: Path) -> None:
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log.open("w", encoding="utf-8") as output:
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd),
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    print(line, end="", flush=True)
                returncode = process.wait()
        except OSError as error:
            raise ReleaseError(f"cannot run {command[0]}: {error}") from error
        if returncode:
            raise ReleaseError(
                f"build command exited {returncode}: {' '.join(command)}; log: {log}"
            )

    def run_task(self, manifest: dict, task: dict) -> dict:
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        evidence_attempt = task.get("evidenceAttempt", 1)
        log = (
            attempt / "build-logs" / f"attempt-{evidence_attempt:03d}"
            / f"{task['id'].replace(':', '-')}.log"
        )
        environment = dict(self.environment)
        environment["CEDAR_HOME"] = str(
            manifest["versionPreparation"][task["variant"]]["workspace"]
        )
        environment["npm_config_cache"] = str(attempt / "build-cache" / "npm")
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        if self.executor is None:
            self._stream_command(task["command"], Path(task["cwd"]), environment, log)
        else:
            log.parent.mkdir(parents=True, exist_ok=True)
            output = self.executor(task, environment)
            log.write_text(output or "", encoding="utf-8")
        record = {
            **task,
            "startedAt": started,
            "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "log": str(log),
            "logSha256": _file_sha256(log),
        }
        if task.get("buildOutput"):
            record["outputFiles"] = _directory_file_hashes(Path(task["buildOutput"]))
        return record

    @staticmethod
    def failed_task_evidence(manifest: dict, task: dict) -> dict:
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        evidence_attempt = task.get("evidenceAttempt", 1)
        log = (
            attempt / "build-logs" / f"attempt-{evidence_attempt:03d}"
            / f"{task['id'].replace(':', '-')}.log"
        )
        result = copy.deepcopy(task)
        if log.is_file():
            result["log"] = str(log)
            result["logSha256"] = _file_sha256(log)
        return result

    @staticmethod
    def verify_completed_task(record: dict) -> None:
        log = Path(record.get("log", ""))
        expected = record.get("logSha256")
        if not log.is_file() or not isinstance(expected, str) or _file_sha256(log) != expected:
            raise ReleaseError(f"completed build evidence is missing or changed for {record.get('id')}")
        if record.get("buildOutput"):
            expected_files = record.get("outputFiles")
            if not isinstance(expected_files, dict) or not expected_files:
                raise ReleaseError(f"completed build has no output evidence for {record.get('id')}")
            if _directory_file_hashes(Path(record["buildOutput"])) != expected_files:
                raise ReleaseError(f"completed build output changed for {record.get('id')}")


class ReleaseRefCreator:
    """Create verified local release refs without pushing them to any remote."""

    def __init__(self, state: ReleaseState, git_runner=None, environment=None):
        self.state = state
        self.environment = dict(os.environ if environment is None else environment)
        self.git = git_runner or ReleaseWorkspacePreparer(
            state, environment=self.environment,
        )

    @staticmethod
    def _expected_files(manifest: dict, variant: str, repository: str) -> dict[str, str]:
        records = manifest["versionPreparation"][variant]["repositories"]
        if repository in records:
            return dict(records[repository]["fileSha256"])
        if variant != "release":
            raise ReleaseError(f"next-development has no prepared record for {repository}")
        expected = {}
        for consumer in manifest["frontendPreparation"]["consumers"]:
            if consumer["repository"] != repository:
                continue
            expected[consumer["manifest"]] = consumer["manifestSha256"]
            expected[consumer["lock"]] = consumer["lockSha256"]
        if not expected:
            raise ReleaseError(f"release has no prepared source record for {repository}")
        return expected

    def tasks(self, manifest: dict) -> list[dict]:
        release_repositories = list(manifest["releaseRepositories"])
        for consumer in manifest["cee"]["consumers"]:
            repository = consumer["repository"]
            if repository not in release_repositories:
                release_repositories.append(repository)
        tasks = []
        variants = manifest["versionPreparation"]
        for variant, repositories, branch, tag in (
            (
                "release",
                release_repositories,
                f"release/pre-{manifest['releaseVersion']}",
                f"release-{manifest['releaseVersion']}",
            ),
            (
                "nextDevelopment",
                manifest["releaseRepositories"],
                f"release/post-{manifest['nextDevelopmentVersion']}",
                None,
            ),
        ):
            workspace = Path(variants[variant]["workspace"])
            for repository in repositories:
                tasks.append({
                    "id": f"{variant}:{repository}",
                    "variant": variant,
                    "repository": repository,
                    "workspace": str(workspace),
                    "branch": branch,
                    "tag": tag,
                    "sourceRevision": manifest["sourceRepositories"][repository],
                    "expectedFiles": self._expected_files(manifest, variant, repository),
                })
        return tasks

    def _ref(self, root: Path, reference: str) -> str | None:
        try:
            return self.git._run([
                "git", "-C", str(root), "rev-parse", "--verify", reference,
            ])
        except ReleaseError:
            return None

    def _identity(self, root: Path) -> tuple[str, str]:
        name = self.environment.get("CEDAR_RELEASE_GIT_NAME")
        email = self.environment.get("CEDAR_RELEASE_GIT_EMAIL")
        if not name:
            try:
                name = self.git._run(["git", "-C", str(root), "config", "user.name"])
            except ReleaseError:
                name = None
        if not email:
            try:
                email = self.git._run(["git", "-C", str(root), "config", "user.email"])
            except ReleaseError:
                email = None
        if not name or not email:
            raise ReleaseError(
                "Git author identity is unavailable; configure git user.name/user.email or "
                "CEDAR_RELEASE_GIT_NAME/CEDAR_RELEASE_GIT_EMAIL"
            )
        return name, email

    def _working_changes(self, root: Path) -> set[str]:
        changed = set(filter(None, self.git._run([
            "git", "-C", str(root), "diff", "--name-only", "HEAD", "--",
        ]).splitlines()))
        untracked = set(filter(None, self.git._run([
            "git", "-C", str(root), "ls-files", "--others", "--exclude-standard",
        ]).splitlines()))
        return changed | untracked

    @staticmethod
    def _verify_file_hashes(root: Path, expected: dict[str, str]) -> None:
        for relative, digest in expected.items():
            if _file_sha256(root / relative) != digest:
                raise ReleaseError(f"prepared release file changed after validation: {root / relative}")

    def _verify_commit(self, root: Path, task: dict, commit: str) -> dict:
        source = task["sourceRevision"]
        parent = self.git._run(["git", "-C", str(root), "rev-parse", f"{commit}^"])
        if parent != source:
            raise ReleaseError(
                f"local {task['variant']} commit for {task['repository']} is not based on {source}"
            )
        changed = set(filter(None, self.git._run([
            "git", "-C", str(root), "diff", "--name-only", source, commit, "--",
        ]).splitlines()))
        expected = set(task["expectedFiles"])
        if changed != expected:
            raise ReleaseError(
                f"local {task['variant']} commit for {task['repository']} has wrong files: "
                f"actual={sorted(changed)}, expected={sorted(expected)}"
            )
        self._verify_file_hashes(root, task["expectedFiles"])
        tree = self.git._run(["git", "-C", str(root), "rev-parse", f"{commit}^{{tree}}"])
        return {
            "id": task["id"],
            "variant": task["variant"],
            "repository": task["repository"],
            "workspace": task["workspace"],
            "branch": task["branch"],
            "tag": task["tag"],
            "sourceRevision": source,
            "commit": commit,
            "tree": tree,
            "changedFiles": sorted(changed),
            "fileSha256": task["expectedFiles"],
        }

    def create(self, manifest: dict, task: dict) -> dict:
        root = Path(task["workspace"]) / task["repository"]
        if not (root / ".git").exists():
            raise ReleaseError(f"prepared repository clone is missing: {root}")
        source = task["sourceRevision"]
        branch_ref = f"refs/heads/{task['branch']}"
        branch_tip = self._ref(root, branch_ref)
        if branch_tip is None:
            head = self.git._run(["git", "-C", str(root), "rev-parse", "HEAD"])
            if head != source:
                raise ReleaseError(f"{root} is at {head}, expected source {source}")
            actual = self._working_changes(root)
            if actual != set(task["expectedFiles"]):
                raise ReleaseError(
                    f"prepared files changed before local commit in {task['repository']}: "
                    f"actual={sorted(actual)}, expected={sorted(task['expectedFiles'])}"
                )
            self._verify_file_hashes(root, task["expectedFiles"])
            self.git._run(["git", "-C", str(root), "switch", "--quiet", "-c", task["branch"]])
            branch_tip = source
        else:
            self.git._run(["git", "-C", str(root), "switch", "--quiet", task["branch"]])

        if branch_tip == source:
            actual = self._working_changes(root)
            if actual != set(task["expectedFiles"]):
                raise ReleaseError(
                    f"local branch has wrong prepared files for {task['repository']}"
                )
            self._verify_file_hashes(root, task["expectedFiles"])
            if task["expectedFiles"]:
                self.git._run([
                    "git", "-C", str(root), "add", "--", *sorted(task["expectedFiles"]),
                ])
            name, email = self._identity(root)
            message = (
                f"Prepare CEDAR {manifest['releaseVersion']} from train {manifest['train']}"
                if task["variant"] == "release"
                else (
                    f"Prepare CEDAR {manifest['nextDevelopmentVersion']} after "
                    f"{manifest['releaseVersion']}"
                )
            )
            self.git._run([
                "git", "-c", f"user.name={name}", "-c", f"user.email={email}",
                "-c", "commit.gpgSign=false", "-C", str(root),
                "commit", "--quiet", "--no-verify", "--allow-empty", "-m", message,
            ])
            branch_tip = self.git._run(["git", "-C", str(root), "rev-parse", "HEAD"])

        record = self._verify_commit(root, task, branch_tip)
        if self._working_changes(root):
            raise ReleaseError(f"local ref creation left tracked changes in {task['repository']}")
        if task["tag"]:
            tag_ref = f"refs/tags/{task['tag']}"
            tag_tip = self._ref(root, tag_ref)
            if tag_tip is None:
                self.git._run([
                    "git", "-C", str(root), "tag", task["tag"], branch_tip,
                ])
                tag_tip = self._ref(root, tag_ref)
            if tag_tip != branch_tip:
                raise ReleaseError(
                    f"local tag {task['tag']} does not identify the prepared release commit"
                )
        record["createdAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return record

    def verify_record(self, manifest: dict, record: dict) -> None:
        task = next((item for item in self.tasks(manifest) if item["id"] == record.get("id")), None)
        if task is None:
            raise ReleaseError(f"recorded local ref task no longer exists: {record.get('id')}")
        root = Path(task["workspace"]) / task["repository"]
        branch_tip = self._ref(root, f"refs/heads/{task['branch']}")
        if branch_tip != record.get("commit"):
            raise ReleaseError(f"recorded local branch changed for {task['repository']}")
        if task["tag"]:
            tag_tip = self._ref(root, f"refs/tags/{task['tag']}")
            if tag_tip != branch_tip:
                raise ReleaseError(f"recorded local tag changed for {task['repository']}")
        verified = self._verify_commit(root, task, branch_tip)
        if verified["tree"] != record.get("tree"):
            raise ReleaseError(f"recorded local tree changed for {task['repository']}")


class ReleaseRemoteIntegrator:
    """Integrate verified release commits into remote main/develop without touching source clones."""

    def __init__(self, state: ReleaseState, git_runner=None, remote_resolver=None, environment=None):
        self.state = state
        self.environment = dict(os.environ if environment is None else environment)
        self.git = git_runner or ReleaseWorkspacePreparer(
            state, environment=self.environment,
        )
        self.ref_creator = ReleaseRefCreator(
            state, git_runner=self.git, environment=self.environment,
        )
        self.remote_resolver = remote_resolver or self._source_remote

    def _source_remote(self, repository: str) -> str:
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        root = Path(cedar_home) / repository
        remote = self.git._run(["git", "-C", str(root), "remote", "get-url", "origin"])
        expected = (
            rf"(?:https://github\.com/metadatacenter/{re.escape(repository)}(?:\.git)?|"
            rf"git@github\.com:metadatacenter/{re.escape(repository)}\.git)"
        )
        if not re.fullmatch(expected, remote):
            raise ReleaseError(f"{repository} origin is not the expected metadatacenter remote")
        return remote

    @staticmethod
    def _by_repository(manifest: dict) -> dict[str, dict]:
        completed = manifest.get("localRefs", {}).get("completedTasks", {})
        if not isinstance(completed, dict):
            raise ReleaseError("release has no verified local refs")
        result: dict[str, dict] = {}
        for record in completed.values():
            repository = record.get("repository")
            variant = record.get("variant")
            if not isinstance(repository, str) or variant not in {"release", "nextDevelopment"}:
                raise ReleaseError("release contains an invalid local ref record")
            if variant in result.setdefault(repository, {}):
                raise ReleaseError(f"release contains duplicate local refs for {repository}")
            result[repository][variant] = record
        expected = {
            task["id"] for task in ReleaseRefCreator(ReleaseState()).tasks(manifest)
        }
        if set(completed) != expected:
            raise ReleaseError("release does not contain the complete verified local ref set")
        return result

    SURVEY_VERSION_FILES = frozenset({
        "pom.xml", "package.json", "package-lock.json", "npm-shrinkwrap.json",
    })

    def survey(self, manifest: dict) -> dict[str, list[str]]:
        """Compare every release remote with the train source before anything is built.

        Both questions this answers are otherwise reached only at remote integration, once
        the release has already spent its Maven and frontend builds: whether a remote
        develop has moved off the train source, which is fatal, and what main carries that
        develop does not, which the release replaces. Answering them from ls-remote costs
        seconds and keeps a stale remote from being discovered hours in.
        """
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        findings: dict[str, list[str]] = {}
        for repository in manifest.get("releaseRepositories", []):
            source = manifest.get("sourceRepositories", {}).get(repository)
            if not source:
                raise ReleaseError(f"{repository} has no recorded train source")
            root = Path(cedar_home) / repository
            remote = self.remote_resolver(repository)
            references = self._remote_refs(
                root, remote, ["refs/heads/main", "refs/heads/develop"],
            )
            develop = references.get("refs/heads/develop")
            main = references.get("refs/heads/main")
            if develop is None or main is None:
                raise ReleaseError(f"{repository} remote must contain main and develop")
            if develop != source:
                raise ReleaseError(
                    f"{repository} develop advanced beyond train source {source}"
                )
            self.git._run([
                "git", "-C", str(root), "fetch", "--quiet", "--no-tags", remote,
                "+refs/heads/main:refs/remotes/cedar-release/survey-main",
                "+refs/heads/develop:refs/remotes/cedar-release/survey-develop",
            ])
            base = self.git._run([
                "git", "-C", str(root), "merge-base",
                "refs/remotes/cedar-release/survey-main",
                "refs/remotes/cedar-release/survey-develop",
            ])
            changed_on_main = set(self.git._run([
                "git", "-C", str(root), "diff", "--name-only", base,
                "refs/remotes/cedar-release/survey-main",
            ]).splitlines())
            changed_on_develop = set(self.git._run([
                "git", "-C", str(root), "diff", "--name-only", base,
                "refs/remotes/cedar-release/survey-develop",
            ]).splitlines())
            replaced = sorted(
                path for path in changed_on_main - changed_on_develop
                if path and PurePosixPath(path).name not in self.SURVEY_VERSION_FILES
            )
            if replaced:
                findings[repository] = replaced
        return findings

    def tasks(self, manifest: dict) -> list[dict]:
        by_repository = self._by_repository(manifest)
        tasks = []
        for repository, records in by_repository.items():
            release = records.get("release")
            if not isinstance(release, dict):
                raise ReleaseError(f"release has no stable local ref for {repository}")
            tasks.append({
                "id": repository,
                "repository": repository,
                "sourceRevision": manifest["sourceRepositories"][repository],
                "release": release,
                "nextDevelopment": records.get("nextDevelopment"),
            })
        return tasks

    def _remote_refs(self, root: Path, remote: str, references: list[str]) -> dict[str, str]:
        output = self.git._run(["git", "-C", str(root), "ls-remote", "--refs", remote, *references])
        result = {}
        for line in output.splitlines():
            commit, reference = line.split("\t", 1)
            if not GIT_SHA_RE.fullmatch(commit):
                raise ReleaseError(f"remote returned an invalid commit for {reference}")
            result[reference] = commit
        return result

    def _integration_commit(
        self,
        root: Path,
        branch: str,
        base: str,
        prepared: dict,
        message: str,
    ) -> str:
        reference = f"refs/heads/{branch}"
        commit = self.ref_creator._ref(root, reference)
        if commit is None:
            # The integration commit must carry the prepared tree exactly, so it is written
            # from that tree rather than merged towards it. A merge preserves whatever the
            # base branch holds and the prepared side does not also change -- a file the
            # base still carries, or one the release deleted -- which silently readmits
            # unreleased content into the release. Building the commit from the prepared
            # tree makes the published branch equal the validated release content, and the
            # base branch keeps that history through the recorded parent.
            name, email = self.ref_creator._identity(root)
            commit = self.git._run([
                "git", "-c", f"user.name={name}", "-c", f"user.email={email}",
                "-c", "commit.gpgSign=false", "-C", str(root), "commit-tree",
                prepared["tree"], "-p", base, "-p", prepared["commit"], "-m", message,
            ])
            self.git._run(["git", "-C", str(root), "branch", "--force", branch, commit])
        parents = self.git._run([
            "git", "-C", str(root), "rev-list", "--parents", "-n", "1", commit,
        ]).split()
        if parents != [commit, base, prepared["commit"]]:
            raise ReleaseError(
                f"integration commit for {prepared['repository']} has unexpected parents"
            )
        tree = self.git._run(["git", "-C", str(root), "rev-parse", f"{commit}^{{tree}}"])
        if tree != prepared["tree"]:
            raise ReleaseError(
                f"integration commit for {prepared['repository']} changed the prepared tree"
            )
        # Publication packs each surface from the workspace's checked-out commit and refuses
        # any workspace whose HEAD is not the integration commit, so leave the workspace on
        # the integration branch. Writing the commit from the prepared tree does not move
        # HEAD by itself the way the merge this replaced did.
        self.git._run(["git", "-C", str(root), "switch", "--quiet", "--force", branch])
        return commit

    def _push(self, root: Path, remote: str, commit: str, reference: str) -> None:
        self.git._run(["git", "-C", str(root), "push", "--porcelain", remote,
                       f"{commit}:{reference}"])

    def integrate(self, manifest: dict, task: dict) -> dict:
        release = task["release"]
        next_development = task.get("nextDevelopment")
        self.ref_creator.verify_record(manifest, release)
        if next_development:
            self.ref_creator.verify_record(manifest, next_development)
        root = Path(release["workspace"]) / task["repository"]
        develop_root = (
            Path(next_development["workspace"]) / task["repository"]
            if next_development else root
        )
        remote = self.remote_resolver(task["repository"])
        release_branch_ref = f"refs/heads/{release['branch']}"
        tag_ref = f"refs/tags/{release['tag']}"
        post_branch_ref = (
            f"refs/heads/{next_development['branch']}" if next_development else None
        )
        queried = ["refs/heads/main", "refs/heads/develop", release_branch_ref, tag_ref]
        if post_branch_ref:
            queried.append(post_branch_ref)
        remote_refs = self._remote_refs(root, remote, queried)
        for fetch_root in {root, develop_root}:
            self.git._run([
                "git", "-C", str(fetch_root), "fetch", "--quiet", "--no-tags", remote,
                "+refs/heads/main:refs/remotes/cedar-release/main",
                "+refs/heads/develop:refs/remotes/cedar-release/develop",
            ])
        source = task["sourceRevision"]
        develop_prepared = next_development or release

        local_main_ref = f"cedar-release/integrate-main-{manifest['releaseVersion']}"
        local_develop_ref = f"cedar-release/integrate-develop-{manifest['releaseVersion']}"
        existing_main = self.ref_creator._ref(root, f"refs/heads/{local_main_ref}")
        existing_develop = self.ref_creator._ref(
            develop_root, f"refs/heads/{local_develop_ref}",
        )
        remote_main = remote_refs.get("refs/heads/main")
        remote_develop = remote_refs.get("refs/heads/develop")
        if remote_main is None or remote_develop is None:
            raise ReleaseError(f"{task['repository']} remote must contain main and develop")
        if existing_develop is None and remote_develop != source:
            raise ReleaseError(
                f"{task['repository']} develop advanced beyond train source {source}"
            )

        main_base = remote_main
        if existing_main is not None and remote_main == existing_main:
            main_base = self.git._run(["git", "-C", str(root), "rev-parse", f"{existing_main}^1"])
        develop_base = source
        main_commit = self._integration_commit(
            root, local_main_ref, main_base, release,
            f"Release CEDAR {manifest['releaseVersion']} from train {manifest['train']}",
        )
        develop_commit = self._integration_commit(
            develop_root, local_develop_ref, develop_base, develop_prepared,
            f"Advance after CEDAR {manifest['releaseVersion']} to "
            f"{manifest['nextDevelopmentVersion']}",
        )

        prepared_refs = [
            (root, release_branch_ref, release["commit"]),
            (root, tag_ref, release["commit"]),
        ]
        if post_branch_ref:
            prepared_refs.append((develop_root, post_branch_ref, next_development["commit"]))
        for push_root, reference, commit in prepared_refs:
            existing = remote_refs.get(reference)
            if existing is not None and existing != commit:
                raise ReleaseError(f"{task['repository']} remote {reference} already differs")
            if existing is None:
                self._push(push_root, remote, commit, reference)
                remote_refs[reference] = commit

        if remote_refs["refs/heads/main"] != main_commit:
            if remote_refs["refs/heads/main"] != main_base:
                raise ReleaseError(f"{task['repository']} main changed during release integration")
            self._push(root, remote, main_commit, "refs/heads/main")
            remote_refs["refs/heads/main"] = main_commit
        if remote_refs["refs/heads/develop"] != develop_commit:
            if remote_refs["refs/heads/develop"] != develop_base:
                raise ReleaseError(f"{task['repository']} develop changed during release integration")
            self._push(develop_root, remote, develop_commit, "refs/heads/develop")
            remote_refs["refs/heads/develop"] = develop_commit

        record = {
            "id": task["id"],
            "repository": task["repository"],
            "remote": remote,
            "sourceRevision": source,
            "releaseBranch": {"ref": release_branch_ref, "commit": release["commit"]},
            "tag": {"ref": tag_ref, "commit": release["commit"]},
            "main": {"base": main_base, "commit": main_commit, "tree": release["tree"]},
            "develop": {
                "base": develop_base,
                "commit": develop_commit,
                "tree": develop_prepared["tree"],
            },
            "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        if post_branch_ref:
            record["postBranch"] = {
                "ref": post_branch_ref, "commit": next_development["commit"],
            }
        self.verify_record(manifest, record)
        return record

    def verify_record(self, manifest: dict, record: dict) -> None:
        task = next((item for item in self.tasks(manifest) if item["id"] == record.get("id")), None)
        if task is None:
            raise ReleaseError(f"recorded remote task no longer exists: {record.get('id')}")
        root = Path(task["release"]["workspace"]) / task["repository"]
        references = [
            record["releaseBranch"]["ref"], record["tag"]["ref"],
            "refs/heads/main", "refs/heads/develop",
        ]
        if record.get("postBranch"):
            references.append(record["postBranch"]["ref"])
        actual = self._remote_refs(root, record["remote"], references)
        expected = {
            record["releaseBranch"]["ref"]: record["releaseBranch"]["commit"],
            record["tag"]["ref"]: record["tag"]["commit"],
            "refs/heads/main": record["main"]["commit"],
            "refs/heads/develop": record["develop"]["commit"],
        }
        if record.get("postBranch"):
            expected[record["postBranch"]["ref"]] = record["postBranch"]["commit"]
        if actual != expected:
            raise ReleaseError(f"remote refs changed after integration for {task['repository']}")


class ReleaseArtifactPublisher:
    """Publish only the already-integrated release trees and verify their registry bytes."""

    def __init__(
        self,
        state: ReleaseState,
        http: HttpClient | None = None,
        environment=None,
        executor=None,
        sleeper=None,
    ):
        self.state = state
        self.environment = dict(os.environ if environment is None else environment)
        self.http = http or HttpClient(environment=self.environment)
        self.executor = executor
        self.sleeper = sleeper or time.sleep

    @staticmethod
    def _integration_record(manifest: dict, repository: str) -> dict:
        record = manifest.get("remoteIntegration", {}).get("completedTasks", {}).get(repository)
        if not isinstance(record, dict):
            raise ReleaseError(f"release has no verified remote integration for {repository}")
        return record

    def tasks(self, manifest: dict) -> list[dict]:
        plan = manifest.get("publicationPlan")
        if not isinstance(plan, dict):
            raise ReleaseError("release manifest has no artifact publication plan")
        maven = plan.get("maven", {})
        npm = plan.get("npm", {})
        phases = manifest.get("mavenPhases")
        if not isinstance(phases, list) or not phases:
            raise ReleaseError("release manifest has no Maven publication phases")
        release_workspace = Path(manifest["versionPreparation"]["release"]["workspace"])
        next_workspace = Path(
            manifest["versionPreparation"]["nextDevelopment"]["workspace"]
        )
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        tasks = [{
            "id": "maven:release:publish",
            "kind": "maven-release-upload",
            "variant": "release",
            "version": manifest["releaseVersion"],
            "repository": maven.get("releaseRepository"),
            "localRepository": str(attempt / "build-cache" / "release" / "m2" / "repository"),
        }, {
            "id": "maven:release:verify",
            "kind": "maven-verify",
            "variant": "release",
            "version": manifest["releaseVersion"],
            "repository": maven.get("releaseRepository"),
            "requiredArtifacts": maven.get("requiredArtifacts"),
        }]
        for surface in npm.get("surfaces", []):
            integration = self._integration_record(manifest, surface["repository"])
            task = {
                **surface,
                "id": f"npm:release:{surface['id']}",
                "kind": "npm-release",
                "variant": "release",
                "version": manifest["releaseVersion"],
                "registry": npm.get("registry"),
                "workspace": str(release_workspace),
                "expectedCommit": integration["main"]["commit"],
                "expectedTree": integration["main"]["tree"],
            }
            if surface.get("buildOutput"):
                task["buildEvidenceId"] = f"release:npm:{surface['id']}:build"
            tasks.append(task)
        return self._checked(tasks)

    @staticmethod
    def _checked(tasks: list[dict]) -> list[dict]:
        identifiers = [task["id"] for task in tasks]
        if len(identifiers) != len(set(identifiers)):
            raise ReleaseError("artifact publication plan contains duplicate tasks")
        return tasks

    @staticmethod
    def _local_ref_record(manifest: dict, variant: str, repository: str) -> dict:
        record = manifest.get("localRefs", {}).get(
            "completedTasks", {}).get(f"{variant}:{repository}")
        if not isinstance(record, dict):
            raise ReleaseError(f"release has no verified {variant} ref for {repository}")
        return record

    def snapshot_tasks(self, manifest: dict) -> list[dict]:
        """Deploy the next-development snapshots, in dependency order, from the prepared trees.

        These run before the remotes are integrated, because integrating them is what makes
        every repository's develop declare the next version, and the CI that each of those
        pushes triggers resolves the parent and libraries at that version from Nexus. Deployed
        afterwards, as they once were, the snapshots arrived minutes too late and left a tail
        of red develop builds that said nothing about the code.

        Running first costs nothing in verification. The trees are the same either way: an
        integration commit is written from the prepared tree and refuses to exist if the
        result differs, so binding to the verified local ref binds to identical bytes.
        """
        plan = manifest.get("publicationPlan")
        if not isinstance(plan, dict):
            raise ReleaseError("release manifest has no artifact publication plan")
        maven = plan.get("maven", {})
        phases = manifest.get("mavenPhases")
        if not isinstance(phases, list) or not phases:
            raise ReleaseError("release manifest has no Maven publication phases")
        next_workspace = Path(
            manifest["versionPreparation"]["nextDevelopment"]["workspace"]
        )
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        tasks = []
        for phase in phases:
            prepared = self._local_ref_record(manifest, "nextDevelopment", phase["repository"])
            root = next_workspace / phase["repository"]
            tasks.append({
                "id": f"maven:nextDevelopment:{phase['name']}",
                "kind": "maven-snapshot-deploy",
                "variant": "nextDevelopment",
                "version": manifest["nextDevelopmentVersion"],
                "repository": phase["repository"],
                "workspace": str(next_workspace),
                "cwd": str(root),
                "expectedCommit": prepared["commit"],
                "expectedTree": prepared["tree"],
                "command": [
                    str(root / "mvnw"), "--batch-mode", "--no-transfer-progress",
                    f"-Dmaven.repo.local={attempt / 'publication-cache' / 'm2' / 'repository'}",
                    "deploy", "-DskipTests", "-DretryFailedDeploymentCount=3",
                ],
            })
        tasks.append({
            "id": "maven:nextDevelopment:verify",
            "kind": "maven-verify",
            "variant": "nextDevelopment",
            "version": manifest["nextDevelopmentVersion"],
            "repository": maven.get("nextDevelopmentRepository"),
            "requiredArtifacts": maven.get("requiredArtifacts"),
        })
        return self._checked(tasks)

    @staticmethod
    def _verify_workspace(task: dict) -> None:
        root = Path(task["workspace"]) / task["repository"]
        try:
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                text=True, capture_output=True,
            ).stdout.strip()
            tree = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], check=True,
                text=True, capture_output=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise ReleaseError(f"cannot verify publication workspace {root}: {error}") from error
        if commit != task["expectedCommit"] or tree != task["expectedTree"]:
            raise ReleaseError(f"publication workspace changed for {task['repository']}")

    @staticmethod
    def _maven_candidates(local_repository: Path, version: str) -> list[Path]:
        group = local_repository / "org" / "metadatacenter"
        candidates = sorted(
            path for path in group.rglob("*")
            if path.is_file()
            and path.parent.name == version
            and not path.name.startswith(".")
            and path.name != "_remote.repositories"
            and not path.name.startswith("maven-metadata")
            and not path.name.endswith((
                ".lastUpdated", ".sha1", ".md5", ".sha256", ".sha512",
            ))
        ) if group.is_dir() else []
        if not candidates:
            raise ReleaseError(f"validated Maven repository has no files for {version}")
        return candidates

    def _upload(self, destination: str, content: bytes) -> None:
        username = self.environment.get("BMIR_NEXUS_USERNAME")
        password = self.environment.get("BMIR_NEXUS_PASSWORD")
        if not username or not password:
            raise ReleaseError("BMIR_NEXUS_USERNAME and BMIR_NEXUS_PASSWORD are required")
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        request = urllib.request.Request(
            destination, data=content, method="PUT",
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/octet-stream"},
        )
        try:
            with self.http.opener(request, timeout=120) as response:
                if response.status not in (200, 201, 204):
                    raise ReleaseError(
                        f"Nexus returned HTTP {response.status} for {destination}"
                    )
        except urllib.error.HTTPError as error:
            raise ReleaseError(f"cannot publish {destination}: HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RetryableReleaseError(f"cannot publish {destination}: {error}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ReleaseError(f"cannot publish {destination}: {error}") from error

    def _publish_maven_release(self, task: dict) -> dict:
        local_repository = Path(task["localRepository"])
        files = {}
        for source in self._maven_candidates(local_repository, task["version"]):
            relative = source.relative_to(local_repository).as_posix()
            content = source.read_bytes()
            destination = task["repository"].rstrip("/") + "/" + relative
            existing = self.http.read(destination, missing_ok=True)
            if existing is None:
                self._upload(destination, content)
            elif existing != content:
                raise ReleaseError(f"immutable Maven release path contains different bytes: {destination}")
            files[relative] = _sha256(content)
        return {
            **task,
            "files": files,
            "publishedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def _nexus_artifacts(self, repository_url: str, version: str) -> set[str]:
        if not isinstance(repository_url, str) or "/repository/" not in repository_url:
            raise ReleaseError("release manifest contains an invalid Maven repository")
        base = repository_url.split("/repository/", 1)[0]
        repository = repository_url.rstrip("/").rsplit("/", 1)[-1]
        continuation = None
        artifacts = set()
        # Nexus indexes a snapshot component under its expanded timestamped version, such as
        # 2.9.4-20260828.215713-1, so searching for the -SNAPSHOT version matches nothing.
        # maven.baseVersion is the field that keeps the base version, and it applies only to
        # snapshots; release versions are indexed under version itself.
        key = "maven.baseVersion" if version.endswith("-SNAPSHOT") else "version"
        while True:
            query = {"repository": repository, key: version}
            if continuation:
                query["continuationToken"] = continuation
            result = self.http.read_json(
                f"{base}/service/rest/v1/search?{urllib.parse.urlencode(query)}"
            )
            assert result is not None
            payload, _ = result
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise ReleaseError("Nexus search returned an invalid artifact inventory")
            artifacts.update(
                item.get("name") for item in items
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            )
            continuation = payload.get("continuationToken")
            if not continuation:
                return artifacts

    def _verify_maven_inventory(self, task: dict, wait: bool) -> dict:
        required = task.get("requiredArtifacts")
        if not isinstance(required, list) or not required:
            raise ReleaseError("Maven publication has no required artifact inventory")
        attempts = 12 if wait else 1
        published = set()
        missing = set(required)
        for attempt in range(attempts):
            published = self._nexus_artifacts(task["repository"], task["version"])
            missing = set(required) - published
            if not missing:
                break
            if attempt + 1 < attempts:
                self.sleeper(10)
        if missing:
            raise ReleaseError(
                f"Nexus is missing required {task['variant']} artifacts: "
                + ", ".join(sorted(missing))
            )
        return {
            **task,
            "verifiedArtifacts": sorted(published),
            "verifiedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    @staticmethod
    def _copy_build_output(source: Path, destination: Path) -> None:
        if not source.is_dir() or not destination.is_dir():
            raise ReleaseError(f"frontend build output is missing: {source}")
        for child in source.iterdir():
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)

    def _npm_version_record(self, registry: str, name: str, version: str) -> dict | None:
        url = registry.rstrip("/") + "/" + urllib.parse.quote(name, safe="")
        result = self.http.read_json(url, missing_ok=True)
        if result is None:
            return None
        metadata, _ = result
        record = metadata.get("versions", {}).get(version)
        return record if isinstance(record, dict) else None

    @staticmethod
    def _npm_tarball_package(identity: str, content: bytes) -> dict:
        try:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
                members = [
                    member for member in archive.getmembers()
                    if PurePosixPath(member.name) == PurePosixPath("package/package.json")
                ]
                if len(members) != 1 or not members[0].isfile():
                    raise ReleaseError(f"{identity} has no unique package/package.json")
                stream = archive.extractfile(members[0])
                if stream is None:
                    raise ReleaseError(f"{identity} package.json is unreadable")
                package = json.load(stream)
        except (tarfile.TarError, json.JSONDecodeError) as error:
            raise ReleaseError(f"{identity} is not a readable npm tarball") from error
        if not isinstance(package, dict):
            raise ReleaseError(f"{identity} package.json is not an object")
        return package

    def _verify_npm_package(self, task: dict, evidence: dict, *, wait: bool) -> dict:
        attempts = 12 if wait else 1
        record = None
        for attempt in range(attempts):
            record = self._npm_version_record(task["registry"], evidence["name"], task["version"])
            if record is not None:
                break
            if attempt + 1 < attempts:
                self.sleeper(10)
        if record is None:
            raise ReleaseError(f"npm registry is missing {evidence['name']}@{task['version']}")
        distribution = record.get("dist", {})
        tarball_url = distribution.get("tarball")
        integrity = distribution.get("integrity")
        if integrity != evidence["integrity"] or not isinstance(tarball_url, str):
            raise ReleaseError(f"npm registry metadata differs for {evidence['name']}@{task['version']}")
        tarball = self.http.read(tarball_url)
        assert tarball is not None
        _verify_integrity(f"{evidence['name']}@{task['version']}", tarball, integrity)
        if _sha256(tarball) != evidence["tarballSha256"]:
            raise ReleaseError(f"npm registry tarball differs for {evidence['name']}@{task['version']}")
        package = self._npm_tarball_package(
            f"{evidence['name']}@{task['version']}", tarball,
        )
        if (
            package.get("name") != evidence["name"]
            or package.get("version") != task["version"]
            or package.get("gitHead") != task["expectedCommit"]
        ):
            raise ReleaseError(f"npm package provenance differs for {evidence['name']}@{task['version']}")
        return {**evidence, "tarball": tarball_url, "verifiedAt": dt.datetime.now(dt.timezone.utc).isoformat()}

    def _pack_npm(self, task: dict) -> tuple[Path, dict]:
        self._verify_workspace(task)
        workspace = Path(task["workspace"])
        root = workspace / task["repository"]
        if task.get("buildOutput"):
            build_record = (
                self.state.read_current_manifest()[0]
                .get("buildValidation", {})
                .get("completedTasks", {})
                .get(task.get("buildEvidenceId"))
            )
            expected_output = str(root / task["buildOutput"])
            if (
                not isinstance(build_record, dict)
                or build_record.get("buildOutput") != expected_output
            ):
                raise ReleaseError(f"release has no build-output proof for {task['id']}")
            ReleaseBuildValidator.verify_completed_task(build_record)
        attempt = Path(workspace).parent
        publication_root = attempt / "publication-packages"
        publication_root.mkdir(parents=True, exist_ok=True)
        npm_environment = dict(self.environment)
        npm_environment["npm_config_cache"] = str(attempt / "publication-cache" / "npm")
        stage = Path(tempfile.mkdtemp(prefix=f"{task['id'].replace(':', '-')}-", dir=publication_root))
        archive = stage / "source.tar"
        try:
            subprocess.run([
                "git", "-C", str(root), "archive", "--format=tar", f"--output={archive}",
                task["expectedCommit"],
            ], check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as error:
            detail = getattr(error, "stderr", None) or str(error)
            raise ReleaseError(f"cannot archive {task['repository']}: {detail.strip()}") from error
        source_root = stage / "source"
        source_root.mkdir()
        try:
            with tarfile.open(archive, mode="r:") as content:
                content.extractall(source_root, filter="data")
        except (OSError, tarfile.TarError) as error:
            raise ReleaseError(f"cannot extract {task['repository']} release source") from error
        package_root = source_root if task["directory"] == "." else source_root / task["directory"]
        if task.get("buildOutput"):
            self._copy_build_output(root / task["buildOutput"], package_root)
        package_path = package_root / "package.json"
        try:
            package = json.loads(package_path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise ReleaseError(f"cannot read staged npm package {package_path}: {error}") from error
        if package.get("version") != task["version"]:
            raise ReleaseError(f"{package_path} does not identify release {task['version']}")
        registry = package.get("publishConfig", {}).get("registry")
        if registry != task["registry"]:
            raise ReleaseError(f"{package_path} does not publish to the planned registry")
        package["gitHead"] = task["expectedCommit"]
        try:
            package_path.write_bytes(_json_bytes(package))
        except OSError as error:
            raise ReleaseError(f"cannot stamp npm provenance in {package_path}: {error}") from error
        try:
            pack = subprocess.run([
                "npm", "pack", str(package_root), "--pack-destination", str(stage),
                "--ignore-scripts", "--json",
            ], check=False, text=True, capture_output=True, env=npm_environment)
        except OSError as error:
            raise ReleaseError(f"cannot run npm pack for {task['id']}: {error}") from error
        if pack.returncode:
            detail = (pack.stderr or pack.stdout).strip()
            raise ReleaseError(f"npm pack failed for {task['id']}: {detail}")
        try:
            packed = json.loads(pack.stdout)
            filename = packed[0]["filename"]
            if not isinstance(filename, str) or PurePosixPath(filename).name != filename:
                raise ReleaseError(f"npm pack returned an unsafe filename for {task['id']}")
            tarball_path = stage / filename
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise ReleaseError(f"npm pack returned invalid evidence for {task['id']}") from error
        try:
            content = tarball_path.read_bytes()
        except OSError as error:
            raise ReleaseError(f"cannot read npm tarball for {task['id']}: {error}") from error
        return tarball_path, {
            "name": package["name"],
            "integrity": "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode(),
            "tarballSha256": _sha256(content),
            "packedTarball": str(tarball_path),
        }

    def _publish_npm(self, task: dict) -> dict:
        tarball, evidence = self._pack_npm(task)
        existing = self._npm_version_record(task["registry"], evidence["name"], task["version"])
        if existing is None:
            command = [
                "npm", "publish", str(tarball), "--tag", "latest",
                "--registry", task["registry"], "--loglevel=notice",
            ]
            npm_environment = dict(self.environment)
            npm_environment["npm_config_cache"] = str(
                Path(task["workspace"]).parent / "publication-cache" / "npm"
            )
            try:
                result = subprocess.run(command, check=False, text=True, env=npm_environment)
            except OSError as error:
                raise ReleaseError(f"cannot run npm publish for {evidence['name']}: {error}") from error
            if result.returncode:
                raise ReleaseError(f"npm publish exited {result.returncode}: {evidence['name']}")
        verified = self._verify_npm_package(task, evidence, wait=True)
        return {**task, **verified, "publishedAt": dt.datetime.now(dt.timezone.utc).isoformat()}

    def _deploy_snapshot(self, manifest: dict, task: dict) -> dict:
        self._verify_workspace(task)
        attempt = Path(manifest["frontendPreparation"]["workspace"]).parent
        log = attempt / "publication-logs" / f"{task['id'].replace(':', '-')}.log"
        environment = dict(self.environment)
        environment["CEDAR_HOME"] = task["workspace"]
        ReleaseBuildValidator._stream_command(
            task["command"], Path(task["cwd"]), environment, log,
        )
        return {
            **task,
            "log": str(log),
            "logSha256": _file_sha256(log),
            "publishedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    def run_task(self, manifest: dict, task: dict) -> dict:
        if self.executor is not None:
            return self.executor(manifest, task)
        if task["kind"] == "maven-release-upload":
            return self._publish_maven_release(task)
        if task["kind"] == "maven-verify":
            return self._verify_maven_inventory(task, wait=True)
        if task["kind"] == "npm-release":
            return self._publish_npm(task)
        if task["kind"] == "maven-snapshot-deploy":
            return self._deploy_snapshot(manifest, task)
        raise ReleaseError(f"unknown artifact publication task {task['kind']}")

    def verify_record(self, manifest: dict, record: dict, tasks: list[dict] | None = None) -> None:
        available = self.tasks(manifest) if tasks is None else tasks
        task = next((item for item in available if item["id"] == record.get("id")), None)
        if task is None or task.get("kind") != record.get("kind"):
            raise ReleaseError(f"recorded publication task no longer exists: {record.get('id')}")
        if self.executor is not None:
            return
        if task["kind"] == "maven-release-upload":
            local_repository = Path(task["localRepository"])
            files = record.get("files")
            if not isinstance(files, dict) or not files:
                raise ReleaseError("recorded Maven release has no file evidence")
            for relative, digest in files.items():
                local = local_repository / relative
                if _file_sha256(local) != digest:
                    raise ReleaseError(f"recorded Maven release input changed: {local}")
                remote = self.http.read(task["repository"].rstrip("/") + "/" + relative)
                if remote is None or _sha256(remote) != digest:
                    raise ReleaseError(f"recorded Maven release artifact changed: {relative}")
        elif task["kind"] == "maven-verify":
            self._verify_maven_inventory(task, wait=False)
        elif task["kind"] == "npm-release":
            self._verify_workspace(task)
            self._verify_npm_package(task, record, wait=False)
        elif task["kind"] == "maven-snapshot-deploy":
            self._verify_workspace(task)
            log = Path(record.get("log", ""))
            if not log.is_file() or _file_sha256(log) != record.get("logSha256"):
                raise ReleaseError(f"recorded publication log changed for {task['id']}")


def validate_active_release_builds(
    state: ReleaseState | None = None,
    validator: ReleaseBuildValidator | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "builds-validated":
        return manifest
    if manifest.get("phase") not in {
        "versions-prepared", "validating-builds", "build-validation-failed",
    }:
        raise ReleaseError(f"cannot validate builds while release is {manifest.get('phase')}")
    validator = validator or ReleaseBuildValidator(state)
    tasks = validator.tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("buildValidation") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "policy": {
            "releaseMavenTests": True,
            "nextDevelopmentMavenTests": False,
            "frontendProductionBuilds": True,
        },
        "completedTasks": {},
    })
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded build evidence does not match the current build plan")
    for record in completed.values():
        validator.verify_completed_task(record)
    evidence["attempt"] = int(evidence.get("attempt", 0)) + 1
    state.update_current_manifest({
        "phase": "validating-builds",
        "buildValidation": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        task = {**task, "evidenceAttempt": evidence["attempt"]}
        try:
            record = validator.run_task(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = validator.failed_task_evidence(manifest, task)
            state.update_current_manifest({
                "phase": "build-validation-failed",
                "buildValidation": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        state.update_current_manifest({
            "phase": "validating-builds",
            "buildValidation": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "builds-validated",
        "buildValidation": evidence,
        "failure": None,
    })
    return completed_manifest


def create_active_release_refs(
    state: ReleaseState | None = None,
    creator: ReleaseRefCreator | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "local-refs-created":
        return manifest
    if manifest.get("phase") not in {
        "builds-validated", "creating-local-refs", "local-ref-creation-failed",
    }:
        raise ReleaseError(f"cannot create local refs while release is {manifest.get('phase')}")
    creator = creator or ReleaseRefCreator(state)
    tasks = creator.tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("localRefs") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pushed": False,
        "completedTasks": {},
    })
    if evidence.get("pushed") is not False:
        raise ReleaseError("local ref evidence must record that no refs were pushed")
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded local refs do not match the current ref plan")
    for record in completed.values():
        creator.verify_record(manifest, record)
    state.update_current_manifest({
        "phase": "creating-local-refs",
        "localRefs": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        try:
            record = creator.create(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = task
            state.update_current_manifest({
                "phase": "local-ref-creation-failed",
                "localRefs": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        state.update_current_manifest({
            "phase": "creating-local-refs",
            "localRefs": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "local-refs-created",
        "localRefs": evidence,
        "failure": None,
    })
    return completed_manifest


def integrate_active_release(
    state: ReleaseState | None = None,
    integrator: ReleaseRemoteIntegrator | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "remote-integrated":
        return manifest
    if manifest.get("phase") not in {
        "snapshots-published", "integrating-remotes", "remote-integration-failed",
    }:
        raise ReleaseError(f"cannot integrate remotes while release is {manifest.get('phase')}")
    integrator = integrator or ReleaseRemoteIntegrator(state)
    tasks = integrator.tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("remoteIntegration") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "completedTasks": {},
    })
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded remote integration does not match the release plan")
    for record in completed.values():
        integrator.verify_record(manifest, record)
    state.update_current_manifest({
        "phase": "integrating-remotes",
        "remoteIntegration": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        try:
            record = integrator.integrate(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = task["id"]
            state.update_current_manifest({
                "phase": "remote-integration-failed",
                "remoteIntegration": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        state.update_current_manifest({
            "phase": "integrating-remotes",
            "remoteIntegration": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "remote-integrated",
        "remoteIntegration": evidence,
        "failure": None,
    })
    return completed_manifest


def publish_active_release_snapshots(
    state: ReleaseState | None = None,
    publisher: ReleaseArtifactPublisher | None = None,
) -> dict:
    """Deploy the next-development snapshots before any remote learns the new version."""
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "snapshots-published":
        return manifest
    if manifest.get("phase") not in {
        "local-refs-created", "publishing-snapshots", "snapshot-publication-failed",
    }:
        raise ReleaseError(f"cannot publish snapshots while release is {manifest.get('phase')}")
    publisher = publisher or ReleaseArtifactPublisher(state)
    tasks = publisher.snapshot_tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("snapshotPublication") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "completedTasks": {},
    })
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded snapshot publication does not match the release plan")
    for record in completed.values():
        publisher.verify_record(manifest, record, tasks)
    state.update_current_manifest({
        "phase": "publishing-snapshots",
        "snapshotPublication": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        try:
            record = publisher.run_task(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = task["id"]
            state.update_current_manifest({
                "phase": "snapshot-publication-failed",
                "snapshotPublication": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        state.update_current_manifest({
            "phase": "publishing-snapshots",
            "snapshotPublication": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "snapshots-published",
        "snapshotPublication": evidence,
        "failure": None,
    })
    return completed_manifest


def publish_active_release(
    state: ReleaseState | None = None,
    publisher: ReleaseArtifactPublisher | None = None,
    remote_integrator: ReleaseRemoteIntegrator | None = None,
    build_validator: ReleaseBuildValidator | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "artifacts-published":
        return manifest
    if manifest.get("phase") not in {
        "remote-integrated", "publishing-artifacts", "artifact-publication-failed",
    }:
        raise ReleaseError(f"cannot publish artifacts while release is {manifest.get('phase')}")
    remote_integrator = remote_integrator or ReleaseRemoteIntegrator(state)
    for record in manifest.get("remoteIntegration", {}).get("completedTasks", {}).values():
        remote_integrator.verify_record(manifest, record)
    build_validator = build_validator or ReleaseBuildValidator(state)
    for record in manifest.get("buildValidation", {}).get("completedTasks", {}).values():
        build_validator.verify_completed_task(record)
    publisher = publisher or ReleaseArtifactPublisher(state)
    tasks = publisher.tasks(manifest)
    task_ids = {task["id"] for task in tasks}
    evidence = copy.deepcopy(manifest.get("artifactPublication") or {
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "completedTasks": {},
    })
    completed = evidence.get("completedTasks")
    if not isinstance(completed, dict) or not set(completed).issubset(task_ids):
        raise ReleaseError("recorded artifact publication does not match the release plan")
    for record in completed.values():
        publisher.verify_record(manifest, record)
    state.update_current_manifest({
        "phase": "publishing-artifacts",
        "artifactPublication": evidence,
        "failure": None,
    })
    for task in tasks:
        if task["id"] in completed:
            continue
        try:
            record = publisher.run_task(manifest, task)
        except ReleaseError as error:
            evidence["failedTask"] = task["id"]
            state.update_current_manifest({
                "phase": "artifact-publication-failed",
                "artifactPublication": evidence,
                "failure": str(error),
            })
            raise
        completed[task["id"]] = record
        evidence.pop("failedTask", None)
        state.update_current_manifest({
            "phase": "publishing-artifacts",
            "artifactPublication": evidence,
            "failure": None,
        })
    evidence["completedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
    completed_manifest, _ = state.update_current_manifest({
        "phase": "artifacts-published",
        "artifactPublication": evidence,
        "failure": None,
    })
    return completed_manifest


def prepare_active_release(
    state: ReleaseState | None = None,
    preparer: ReleaseWorkspacePreparer | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "frontends-prepared":
        return manifest
    if manifest.get("phase") not in {"started", "frontend-preparation-failed"}:
        raise ReleaseError(f"cannot prepare frontends while release is {manifest.get('phase')}")
    preparer = preparer or ReleaseWorkspacePreparer(state)
    attempt = preparer.next_attempt(manifest["releaseVersion"])
    state.update_current_manifest({
        "phase": "preparing-frontends",
        "lastAttempt": str(attempt),
        "failure": None,
    })
    try:
        result = preparer.prepare(manifest, attempt)
    except ReleaseError as error:
        state.update_current_manifest({
            "phase": "frontend-preparation-failed",
            "lastAttempt": str(attempt),
            "failure": str(error),
        })
        raise
    completed, _ = state.update_current_manifest({
        "phase": "frontends-prepared",
        "frontendPreparation": result,
        "failure": None,
    })
    return completed


def prepare_active_release_versions(
    state: ReleaseState | None = None,
    preparer: ReleaseVersionPreparer | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "versions-prepared":
        return manifest
    if manifest.get("phase") != "frontends-prepared":
        raise ReleaseError(f"cannot stamp versions while release is {manifest.get('phase')}")
    preparer = preparer or ReleaseVersionPreparer(state)
    state.update_current_manifest({"phase": "preparing-versions", "failure": None})
    try:
        result = preparer.prepare(manifest)
    except ReleaseError as error:
        state.update_current_manifest({
            "phase": "version-preparation-failed",
            "failure": str(error),
        })
        raise
    completed, _ = state.update_current_manifest({
        "phase": "versions-prepared",
        "versionPreparation": result,
        "failure": None,
    })
    return completed


class ReleaseAcceptance:
    """Prove a finished release from outside the ledger that recorded it.

    Each phase verifies its own work as it goes, but nothing until now asked whether the
    release as a whole holds once every phase has run. That question was answered by hand
    for 2.9.3, and an answer given by hand is one a release cannot be left alone to reach.
    """

    def __init__(
        self,
        state: ReleaseState,
        *,
        remote_integrator: "ReleaseRemoteIntegrator | None" = None,
        publisher: "ReleaseArtifactPublisher | None" = None,
        environment=None,
    ):
        self.state = state
        self.environment = dict(os.environ if environment is None else environment)
        self.remote_integrator = remote_integrator or ReleaseRemoteIntegrator(
            state, environment=self.environment)
        self.publisher = publisher or ReleaseArtifactPublisher(state)

    def _check(self, name: str, detail: str) -> dict:
        return {"check": name, "detail": detail}

    def _remote_state_still_holds(self, manifest: dict) -> list[dict]:
        records = manifest.get("remoteIntegration", {}).get("completedTasks", {})
        for record in records.values():
            self.remote_integrator.verify_record(manifest, record)
        return [self._check(
            "remote-integration",
            f"{len(records)} repositories still carry the integrated refs")]

    def _published_artifacts_still_hold(self, manifest: dict) -> list[dict]:
        records = manifest.get("artifactPublication", {}).get("completedTasks", {})
        for record in records.values():
            self.publisher.verify_record(manifest, record)
        snapshots = manifest.get("snapshotPublication", {}).get("completedTasks", {})
        snapshot_tasks = self.publisher.snapshot_tasks(manifest) if snapshots else []
        for record in snapshots.values():
            self.publisher.verify_record(manifest, record, snapshot_tasks)
        return [self._check(
            "artifact-publication",
            f"{len(records)} release and {len(snapshots)} snapshot publication tasks still "
            "match their published bytes")]

    def _every_repository_carries_the_tag(self, manifest: dict) -> list[dict]:
        """Ask each remote directly, rather than trusting the tasks that wrote the tags.

        A repository absent from the integration set would otherwise leave the release
        looking complete while carrying no tag at all.
        """
        tag = f"release-{manifest['releaseVersion']}"
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        untagged = []
        for repository in manifest.get("releaseRepositories", []):
            root = Path(cedar_home) / repository
            remote = self.remote_integrator.remote_resolver(repository)
            references = self.remote_integrator._remote_refs(
                root, remote, [f"refs/tags/{tag}"])
            if not references.get(f"refs/tags/{tag}"):
                untagged.append(repository)
        if untagged:
            raise ReleaseError(
                f"{tag} is absent from " + ", ".join(sorted(untagged)))
        return [self._check(
            "release-tag",
            f"{tag} present in all {len(manifest.get('releaseRepositories', []))} repositories")]

    def _consumers_pin_the_proven_cee(self, manifest: dict) -> list[dict]:
        cee = manifest["cee"]
        expected = cee["public"]["version"]
        consumers = cee.get("consumers", [])
        for consumer in consumers:
            repository = consumer["repository"]
            if repository not in manifest.get("releaseRepositories", []):
                continue
            record = manifest.get("remoteIntegration", {}).get("completedTasks", {})
            if not any(item.get("repository") == repository for item in record.values()):
                raise ReleaseError(f"{repository} was never integrated, so its CEE pin is unproven")
        return [self._check(
            "cee-pin",
            f"{len(consumers)} consumer(s) pin the proven public CEE {expected}")]

    def run(self, manifest: dict) -> dict:
        checks = []
        checks.extend(self._remote_state_still_holds(manifest))
        checks.extend(self._published_artifacts_still_hold(manifest))
        checks.extend(self._every_repository_carries_the_tag(manifest))
        checks.extend(self._consumers_pin_the_proven_cee(manifest))
        return {
            "acceptedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "checks": checks,
        }


def accept_active_release(
    state: ReleaseState | None = None,
    acceptance: ReleaseAcceptance | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "accepted":
        return manifest
    if manifest.get("phase") not in {"artifacts-published", "acceptance-failed"}:
        raise ReleaseError(f"cannot accept a release that is {manifest.get('phase')}")
    acceptance = acceptance or ReleaseAcceptance(state)
    try:
        evidence = acceptance.run(manifest)
    except ReleaseError as error:
        state.update_current_manifest({
            "phase": "acceptance-failed",
            "failure": str(error),
        })
        raise
    completed_manifest, _ = state.update_current_manifest({
        "phase": "accepted",
        "acceptance": evidence,
        "failure": None,
    })
    state.conclude()
    return completed_manifest


@dataclasses.dataclass(frozen=True)
class ReleaseStage:
    """One step of the release: where it may start from, and what it records when it finishes.

    The stages were a chain of conditionals, each branch repeating the tail of the one below
    it, so adding a step meant editing every branch and forgetting one stranded a resumed
    release at that step. Ordering them instead makes resumption a search for the first stage
    that can still take the recorded phase, and adding a step a single entry in this list.
    """

    name: str
    entry_phases: frozenset[str]
    done_phase: str
    run: object

    def __call__(self, state: "ReleaseState", dependencies: dict) -> dict:
        return self.run(state, dependencies)


# A partial stamping attempt is evidence, not a state to resume into: the version preparer
# rewrites files across every repository, and continuing from half of that would stamp some
# twice. Both phases therefore rewind to the frontend stage, which starts a fresh attempt.
REWIND_TO_FRONTENDS = frozenset({"preparing-versions", "version-preparation-failed"})

RELEASE_STAGES = (
    ReleaseStage(
        "frontends",
        frozenset({"started", "preparing-frontends", "frontend-preparation-failed"}),
        "frontends-prepared",
        lambda state, deps: prepare_active_release(state, deps["workspace_preparer"]),
    ),
    ReleaseStage(
        "versions",
        frozenset({"frontends-prepared"}),
        "versions-prepared",
        lambda state, deps: prepare_active_release_versions(state, deps["version_preparer"]),
    ),
    ReleaseStage(
        "builds",
        frozenset({"versions-prepared", "validating-builds", "build-validation-failed"}),
        "builds-validated",
        lambda state, deps: validate_active_release_builds(state, deps["build_validator"]),
    ),
    ReleaseStage(
        "local-refs",
        frozenset({"builds-validated", "creating-local-refs", "local-ref-creation-failed"}),
        "local-refs-created",
        lambda state, deps: create_active_release_refs(state, deps["ref_creator"]),
    ),
    ReleaseStage(
        "snapshots",
        frozenset({
            "local-refs-created", "publishing-snapshots", "snapshot-publication-failed",
        }),
        "snapshots-published",
        lambda state, deps: publish_active_release_snapshots(
            state, deps["artifact_publisher"]),
    ),
    ReleaseStage(
        "remotes",
        frozenset({"snapshots-published", "integrating-remotes", "remote-integration-failed"}),
        "remote-integrated",
        lambda state, deps: integrate_active_release(state, deps["remote_integrator"]),
    ),
    ReleaseStage(
        "artifacts",
        frozenset({"remote-integrated", "publishing-artifacts", "artifact-publication-failed"}),
        "artifacts-published",
        lambda state, deps: publish_active_release(
            state, deps["artifact_publisher"], deps["remote_integrator"],
            deps["build_validator"],
        ),
    ),
    ReleaseStage(
        "acceptance",
        frozenset({"artifacts-published", "acceptance-failed"}),
        "accepted",
        lambda state, deps: accept_active_release(state, deps["acceptance"]),
    ),
)

RELEASE_TERMINAL_PHASE = RELEASE_STAGES[-1].done_phase


def advance_active_release(
    state: ReleaseState | None = None,
    workspace_preparer: ReleaseWorkspacePreparer | None = None,
    version_preparer: ReleaseVersionPreparer | None = None,
    build_validator: ReleaseBuildValidator | None = None,
    ref_creator: ReleaseRefCreator | None = None,
    remote_integrator: ReleaseRemoteIntegrator | None = None,
    artifact_publisher: ReleaseArtifactPublisher | None = None,
    acceptance: ReleaseAcceptance | None = None,
) -> dict:
    """Run the release from the first stage that can still take its recorded phase."""
    state = state or ReleaseState()
    dependencies = {
        "workspace_preparer": workspace_preparer,
        "version_preparer": version_preparer,
        "build_validator": build_validator,
        "ref_creator": ref_creator,
        "remote_integrator": remote_integrator,
        "artifact_publisher": artifact_publisher,
        "acceptance": acceptance,
    }
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") in REWIND_TO_FRONTENDS:
        manifest, _ = state.update_current_manifest({"phase": "frontend-preparation-failed"})
    phase = manifest.get("phase")
    if phase == RELEASE_TERMINAL_PHASE:
        return manifest
    start = next(
        (index for index, stage in enumerate(RELEASE_STAGES) if phase in stage.entry_phases),
        None,
    )
    if start is None:
        raise ReleaseError(f"a release in {phase} has no stage that can continue it")
    for stage in RELEASE_STAGES[start:]:
        manifest = stage(state, dependencies)
    return manifest


# Variables the CEDAR profile defines and the Maven suites read. A build started without
# them fails deep inside Dropwizard configuration with UndefinedEnvironmentVariableException.
PROFILE_REQUIRED_VARIABLES = (
    "CEDAR_HOME",
    "CEDAR_HOST",
    "CEDAR_DEVELOP_HOME",
    "CEDAR_NET_GATEWAY",
    "CEDAR_FRONTEND_TARGET",
)
PROFILE_COMMAND = "source $CEDAR_HOME/cedar-profile-native-develop.sh"

REQUIRED_TOOLS = ("git", "mvn", "node", "npm")
REQUIRED_JAVA_MAJOR = 17
# A train, its attempt tree and the archives of earlier attempts.
REQUIRED_FREE_BYTES = 40 * 1024 ** 3

NEXUS_HOST = "https://nexus.bmir.stanford.edu"
NEXUS_NPM_REGISTRY = f"{NEXUS_HOST}/repository/npm-cedar/"
# Anonymous callers receive 403 from this endpoint, so a 200 proves the configured
# credentials authenticate. It does not prove the deploy privilege on a given
# repository, which only a write can establish.
NEXUS_AUTHENTICATED_ENDPOINT = f"{NEXUS_HOST}/service/rest/v1/status/check"
NEXUS_WRITABLE_ENDPOINT = f"{NEXUS_HOST}/service/rest/v1/status/writable"

# Files a Maven build regenerates with the project version inside. Every match must be
# declared in MAVEN_GENERATED_VERSION_FILES, or the prepared-file guard trips mid-build.
GENERATED_VERSION_FILE_GLOBS = (
    "*/src/main/resources/assets/swagger-api/swagger.json",
    "*/src/main/resources/assets/swagger-api/swagger.yaml",
)



@dataclasses.dataclass(frozen=True)
class PreflightFinding:
    """One settled precondition, carrying the action that clears it."""

    check: str
    severity: str
    message: str
    remedy: str = ""

    @property
    def fatal(self) -> bool:
        return self.severity == "fail"


class ReleasePreflight:
    """Settle every release precondition that is knowable before the first build.

    The 2.9.3 release failed five times, and each failure was a condition that already
    held when the release started: undeclared generated files, absent Nexus credentials,
    a blocked push to main, a red develop, and content carried only on main. Each cost
    the hours of Maven and frontend building that preceded the phase that noticed. Every
    check here answers one of those questions from local state or a cheap remote read, so
    a release either refuses in its first minute or runs with its preconditions settled.
    """

    CHECKS = (
        "check_no_release_in_progress",
        "check_toolchain",
        "check_profile",
        "check_disk_space",
        "check_working_trees",
        "check_nexus_authorization",
        "check_npm_authorization",
        "check_push_permission",
        "check_target_version_unused",
        "check_develop_is_green",
        "check_generated_version_files",
        "check_license_files",
        "check_remote_survey",
    )

    def __init__(
        self,
        manifest: dict,
        *,
        state: "ReleaseState | None" = None,
        command_runner=None,
        http: HttpClient | None = None,
        environment=None,
        accepted_red_develop: dict[str, str] | None = None,
    ):
        self.manifest = manifest
        self.environment = dict(os.environ if environment is None else environment)
        self.state = state or ReleaseState()
        self.command_runner = command_runner or subprocess.run
        self.http = http or HttpClient(environment=self.environment)
        self.accepted_red_develop = dict(accepted_red_develop or {})

    @property
    def repositories(self) -> list[str]:
        return list(self.manifest.get("releaseRepositories", []))

    def _root(self, repository: str) -> Path:
        cedar_home = self.environment.get("CEDAR_HOME")
        if not cedar_home:
            raise ReleaseError("CEDAR_HOME is not set")
        return Path(cedar_home) / repository

    def _capture(self, args: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
        """Run a command and report its outcome instead of raising on failure.

        A failing command is the answer to several checks rather than an error, so
        preflight needs the return code where the release runner needs an exception.
        """
        try:
            result = self.command_runner(
                args,
                cwd=str(cwd) if cwd else None,
                env=self.environment,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            return 127, "", str(error)
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()

    def run(self) -> list[PreflightFinding]:
        findings: list[PreflightFinding] = []
        for name in self.CHECKS:
            findings.extend(getattr(self, name)())
        return findings

    def check_no_release_in_progress(self) -> list[PreflightFinding]:
        """A release already holds the slot, and start would refuse only after planning."""
        try:
            current = self.state.read_current()
        except ReleaseError:
            return []
        if current.get("concludedAt"):
            return []
        active = current.get("releaseVersion")
        if active == self.manifest.get("releaseVersion"):
            return [PreflightFinding(
                "state", "fail", f"release {active} is already active",
                "cedarcli release resume, or cedarcli release status",
            )]
        return [PreflightFinding(
            "state", "fail",
            f"release {active} is still the active release and has not concluded",
            f"finish it with cedarcli release resume, or release the slot with "
            f"cedarcli release conclude once it has reached its terminal phase",
        )]

    def check_toolchain(self) -> list[PreflightFinding]:
        findings = []
        for tool in REQUIRED_TOOLS:
            if shutil.which(tool, path=self.environment.get("PATH")) is None:
                findings.append(PreflightFinding(
                    "toolchain", "fail", f"{tool} is not on PATH",
                    f"install {tool} and make it available to the release shell",
                ))
        code, _, stderr = self._capture(["java", "-version"])
        if code != 0:
            findings.append(PreflightFinding(
                "toolchain", "fail", "java is not on PATH",
                'export JAVA_HOME=$(/usr/libexec/java_home -v 17)',
            ))
            return findings
        match = re.search(r'version "(\d+)', stderr)
        major = int(match.group(1)) if match else None
        if major != REQUIRED_JAVA_MAJOR:
            findings.append(PreflightFinding(
                "toolchain", "fail",
                f"Java {major or 'of unknown version'} is active, and CEDAR builds require "
                f"Java {REQUIRED_JAVA_MAJOR}",
                'export JAVA_HOME=$(/usr/libexec/java_home -v 17)',
            ))
        return findings

    def check_profile(self) -> list[PreflightFinding]:
        missing = [name for name in PROFILE_REQUIRED_VARIABLES if not self.environment.get(name)]
        if not missing:
            return []
        return [PreflightFinding(
            "profile", "fail",
            "the CEDAR profile is not sourced, so " + ", ".join(missing) + " are undefined",
            PROFILE_COMMAND,
        )]

    def check_disk_space(self) -> list[PreflightFinding]:
        try:
            free = shutil.disk_usage(self.state.root.parent).free
        except OSError as error:
            return [PreflightFinding("disk", "warn", f"cannot measure free space: {error}")]
        if free >= REQUIRED_FREE_BYTES:
            return []
        return [PreflightFinding(
            "disk", "fail",
            f"{free // 1024 ** 3} GiB free, and a release needs "
            f"{REQUIRED_FREE_BYTES // 1024 ** 3} GiB for the train, the attempt tree and archives",
            "free space or archive earlier release attempts",
        )]

    def check_working_trees(self) -> list[PreflightFinding]:
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                findings.append(PreflightFinding(
                    "working-tree", "fail", f"{repository} is not checked out at {root}",
                    f"cedarcli git clone {repository}",
                ))
                continue
            code, branch, _ = self._capture(
                ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"])
            if code != 0:
                findings.append(PreflightFinding(
                    "working-tree", "fail", f"{repository} is not a readable git repository"))
                continue
            if branch != "develop":
                findings.append(PreflightFinding(
                    "working-tree", "fail", f"{repository} is on {branch} rather than develop",
                    f"git -C {root} switch develop",
                ))
            # Untracked files are ordinary in a development tree, and the release builds from
            # the train's commits rather than from this one. A modified tracked file is the
            # signal worth blocking on: it is work someone may believe is in the release.
            _, dirty, _ = self._capture([
                "git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"])
            if dirty:
                count = len(dirty.splitlines())
                findings.append(PreflightFinding(
                    "working-tree", "fail",
                    f"{repository} has {count} uncommitted change(s)",
                    f"commit or stash them in {root}",
                ))
            code, ahead, _ = self._capture(
                ["git", "-C", str(root), "rev-list", "--count", "@{upstream}..HEAD"])
            if code == 0 and ahead.isdigit() and int(ahead) > 0:
                findings.append(PreflightFinding(
                    "working-tree", "fail",
                    f"{repository} has {ahead} unpushed commit(s) on develop",
                    f"git -C {root} push",
                ))
        return findings

    def check_nexus_authorization(self) -> list[PreflightFinding]:
        username = self.environment.get("BMIR_NEXUS_USERNAME")
        password = self.environment.get("BMIR_NEXUS_PASSWORD")
        if not username or not password:
            return [PreflightFinding(
                "nexus", "fail",
                "BMIR_NEXUS_USERNAME and BMIR_NEXUS_PASSWORD are not both set, and Nexus "
                "reads fall back to anonymous, so nothing else reveals this until the "
                "first upload",
                "export both from the bmir-nexus-releases server in ~/.m2/settings.xml",
            )]
        findings = []
        try:
            self.http.read(NEXUS_AUTHENTICATED_ENDPOINT)
        except ReleaseError as error:
            findings.append(PreflightFinding(
                "nexus", "fail",
                f"BMIR_NEXUS_USERNAME does not authenticate against Nexus: {error}",
                "check the credentials against the bmir-nexus-releases server entry",
            ))
            return findings
        try:
            self.http.read(NEXUS_WRITABLE_ENDPOINT)
        except ReleaseError as error:
            findings.append(PreflightFinding(
                "nexus", "fail", f"Nexus does not report itself writable: {error}",
                "wait for Nexus to return to a writable state",
            ))
        return findings

    def check_npm_authorization(self) -> list[PreflightFinding]:
        code, _, stderr = self._capture(
            ["npm", "whoami", "--registry", NEXUS_NPM_REGISTRY])
        if code == 0:
            return []
        return [PreflightFinding(
            "npm", "fail",
            f"npm is not authenticated against {NEXUS_NPM_REGISTRY}: {stderr.splitlines()[-1] if stderr else 'no identity'}",
            f"npm login --registry {NEXUS_NPM_REGISTRY}",
        )]

    def check_push_permission(self) -> list[PreflightFinding]:
        """Ask each remote whether the release's own writes would be accepted.

        A release writes main and a tag in every repository. A branch protection rule or a
        lapsed token refuses those at remote integration, after the build phase has already
        run, so the question is asked here with a push that transmits nothing.
        """
        findings = []
        tag = f"release-{self.manifest.get('releaseVersion')}"
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                continue
            source = self.manifest.get("sourceRepositories", {}).get(repository)
            if not source:
                continue
            code, _, stderr = self._capture([
                "git", "-C", str(root), "push", "--dry-run", "--force", "origin",
                f"{source}:refs/heads/main", f"{source}:refs/tags/{tag}",
            ])
            if code != 0:
                detail = stderr.splitlines()[-1] if stderr else "push refused"
                findings.append(PreflightFinding(
                    "push", "fail",
                    f"{repository} refuses the release's writes to main and {tag}: {detail}",
                    "grant push access, or lift the branch protection on main",
                ))
        return findings

    def check_target_version_unused(self) -> list[PreflightFinding]:
        version = self.manifest.get("releaseVersion")
        tag = f"release-{version}"
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                continue
            code, output, _ = self._capture(
                ["git", "-C", str(root), "ls-remote", "--tags", "origin", f"refs/tags/{tag}"])
            if code == 0 and output:
                findings.append(PreflightFinding(
                    "version", "fail",
                    f"{repository} already carries {tag}",
                    f"choose an unused release version, or delete {tag} deliberately",
                ))
        return findings

    def check_develop_is_green(self) -> list[PreflightFinding]:
        """Refuse to release a source commit that its own CI reports broken.

        The question is asked of the exact commit the train was built from, not of whatever
        develop points at now. That is both the more precise question and the stable one: a
        release advances develop to the next snapshot in every repository at once, and the
        CI those pushes trigger can race the parent snapshot they depend on, leaving a tail
        of red runs that say nothing about the source being released.
        """
        if shutil.which("gh", path=self.environment.get("PATH")) is None:
            return [PreflightFinding(
                "ci", "fail", "gh is not on PATH, so the source commit's CI state cannot be read",
                "install the GitHub CLI and authenticate it with gh auth login",
            )]
        findings = []
        for repository in self.repositories:
            source = self.manifest.get("sourceRepositories", {}).get(repository)
            if not source:
                continue
            code, output, stderr = self._capture([
                "gh", "api",
                f"repos/metadatacenter/{repository}/actions/runs"
                f"?head_sha={source}&per_page=20",
                # A run still in flight has a null conclusion, and an empty leading field
                # would be lost to the surrounding strip, so name it.
                "--jq", '.workflow_runs[] | [.conclusion // "pending", .status, .id, .name]'
                        " | @tsv",
            ])
            if code != 0:
                findings.append(PreflightFinding(
                    "ci", "warn",
                    f"{repository} CI state is unreadable: "
                    f"{stderr.splitlines()[-1] if stderr else code}",
                ))
                continue
            if not output:
                findings.append(PreflightFinding(
                    "ci", "warn",
                    f"{repository} has no CI run for the train source {source[:8]}",
                ))
                continue
            for line in output.splitlines():
                conclusion, status, run_id, name = (line.split("\t") + ["", "", "", ""])[:4]
                if status != "completed":
                    findings.append(PreflightFinding(
                        "ci", "warn",
                        f"{repository} {name or 'CI'} is still {status} for {source[:8]}",
                    ))
                    continue
                if conclusion in {"success", "skipped", "neutral"}:
                    continue
                if self.accepted_red_develop.get(repository) == run_id:
                    findings.append(PreflightFinding(
                        "ci", "warn",
                        f"{repository} develop is {conclusion} in run {run_id}, accepted explicitly",
                    ))
                    continue
                findings.append(PreflightFinding(
                    "ci", "fail",
                    f"{repository} {name or 'CI'} is {conclusion} for the train source "
                    f"{source[:8]} in run {run_id}",
                    f"fix develop and build a new train, or accept this run with "
                    f"--accept-red-develop {repository}={run_id}",
                ))
        return findings

    def check_generated_version_files(self) -> list[PreflightFinding]:
        """Find version-bearing generated files the stamping table does not declare.

        An undeclared file is regenerated during the build with the release version inside,
        which the prepared-file guard then reports as drift. Declaring it is the fix, and
        finding it here costs a directory walk rather than a build.
        """
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                continue
            declared = set(MAVEN_GENERATED_VERSION_FILES.get(repository, {}))
            for glob in GENERATED_VERSION_FILE_GLOBS:
                for path in sorted(root.glob(glob)):
                    relative = path.relative_to(root).as_posix()
                    if relative in declared:
                        continue
                    findings.append(PreflightFinding(
                        "generated-files", "fail",
                        f"{repository} regenerates {relative}, which carries the version and "
                        "is not declared",
                        f"add {relative} to MAVEN_GENERATED_VERSION_FILES[{repository!r}]",
                    ))
        return findings

    def check_license_files(self) -> list[PreflightFinding]:
        findings = []
        for repository in self.repositories:
            root = self._root(repository)
            if not root.is_dir():
                continue
            path = root / LICENSE_FILE_NAME
            if not path.is_file():
                findings.append(PreflightFinding(
                    "license", "warn",
                    f"{repository} has no {LICENSE_FILE_NAME}, so its copyright year is not stamped",
                ))
                continue
            if not LICENSE_COPYRIGHT_RE.search(path.read_text(encoding="utf-8", errors="replace")):
                findings.append(PreflightFinding(
                    "license", "fail",
                    f"{repository} has a {LICENSE_FILE_NAME} with no recognisable copyright year",
                    f"restore the 'Copyright (c) YYYY,' line in {path}",
                ))
        return findings

    def check_remote_survey(self) -> list[PreflightFinding]:
        try:
            replaced = ReleaseRemoteIntegrator(self.state, environment=self.environment).survey(
                self.manifest)
        except ReleaseError as error:
            return [PreflightFinding("remote", "fail", str(error))]
        findings = []
        for repository, paths in sorted(replaced.items()):
            findings.append(PreflightFinding(
                "remote", "warn",
                f"{repository} carries {len(paths)} file(s) on main alone, which the release "
                "replaces: " + ", ".join(paths),
            ))
        return findings


def _render_plan(manifest: dict) -> None:
    cee = manifest["cee"]
    console.print(f"Release:             {manifest['releaseVersion']}")
    console.print(f"Next development:    {manifest['nextDevelopmentVersion']}")
    console.print(f"Source train:        {manifest['train']}")
    console.print(
        "CEE equivalence:     "
        f"{cee['development']['version']} -> {cee['public']['version']}"
    )
    console.print(f"CEE payload SHA-256: {cee['promotionProof']['normalizedPayloadSha256']}")
    console.print("CEE executable:      identical after declared release-provenance changes")


def _build_or_exit(
    release_version: str,
    next_version: str,
    from_train: str,
    cee_version: str,
) -> dict:
    try:
        return ReleasePlanner().build(
            release_version=release_version,
            next_version=next_version,
            train=from_train,
            cee_version=cee_version,
        )
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error


UNATTENDED_ATTEMPTS = 5
UNATTENDED_BACKOFF_SECONDS = (30, 60, 120, 300)


def _drive_release(state: ReleaseState, *, unattended: bool, sleeper=time.sleep) -> dict:
    """Advance the release, absorbing transport faults when nobody is watching.

    A release runs for hours across two registries and forty remotes, so an unattended one
    that stopped at the first refused connection would not be unattended at all. Only
    RetryableReleaseError is retried, and only a transport raises it, so a guard still
    stops the release on its first refusal.
    """
    for attempt in range(1, UNATTENDED_ATTEMPTS + 1):
        try:
            return advance_active_release(state)
        except RetryableReleaseError:
            if not unattended or attempt == UNATTENDED_ATTEMPTS:
                raise
            delay = UNATTENDED_BACKOFF_SECONDS[
                min(attempt - 1, len(UNATTENDED_BACKOFF_SECONDS) - 1)]
            console.print(
                f"[yellow]Transient failure on attempt {attempt}; retrying in {delay}s[/yellow]")
            sleeper(delay)
    raise ReleaseError("unattended release exhausted its retries")


def _parse_accepted_red_develop(values: list[str] | None) -> dict[str, str]:
    accepted = {}
    for value in values or []:
        repository, separator, run_id = value.partition("=")
        if not separator or not repository.strip() or not run_id.strip():
            console.print(
                f"[red]--accept-red-develop expects <repository>=<run-id>, not {value!r}[/red]")
            raise typer.Exit(1)
        accepted[repository.strip()] = run_id.strip()
    return accepted


def _preflight_or_exit(manifest: dict, accepted_red_develop: dict[str, str]) -> None:
    """Report every settled precondition, and stop before any state changes if one failed."""
    try:
        findings = ReleasePreflight(
            manifest, accepted_red_develop=accepted_red_develop,
        ).run()
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    failures = [finding for finding in findings if finding.fatal]
    warnings = [finding for finding in findings if not finding.fatal]
    if not findings:
        console.print("Preflight:           every precondition settled")
    else:
        console.print(
            f"Preflight:           {len(failures)} blocking, {len(warnings)} advisory")
    for finding in warnings:
        console.print(f"  [yellow]{finding.check}: {finding.message}[/yellow]")
    for finding in failures:
        console.print(f"  [red]{finding.check}: {finding.message}[/red]")
        if finding.remedy:
            console.print(f"    {finding.remedy}")
    if failures:
        console.print(
            f"[red]{len(failures)} precondition(s) block this release. Nothing was changed."
            "[/red]")
        raise typer.Exit(1)


UNATTENDED_HELP = (
    "Retry transport faults with backoff so a long release survives a network blip"
)
ACCEPT_RED_DEVELOP_HELP = (
    "Accept one repository's red develop by naming the exact run, as <repository>=<run-id>"
)


@app.command("plan")
def plan(
    release_version: str = typer.Option(..., "--version", help="Explicit CEDAR release version"),
    next_version: str = typer.Option(..., "--next-version", help="Explicit next SNAPSHOT version"),
    from_train: str = typer.Option(..., "--from-train", help="Completed development build train"),
    cee_version: str = typer.Option(..., "--cee-version", help="Exact public npmjs CEE version"),
    accept_red_develop: list[str] = typer.Option(
        None, "--accept-red-develop", help=ACCEPT_RED_DEVELOP_HELP),
):
    """Settle every release precondition without changing release state."""
    manifest = _build_or_exit(release_version, next_version, from_train, cee_version)
    _render_plan(manifest)
    _preflight_or_exit(manifest, _parse_accepted_red_develop(accept_red_develop))
    console.print("No changes made.")


@app.command("start")
def start(
    release_version: str = typer.Option(..., "--version", help="Explicit CEDAR release version"),
    next_version: str = typer.Option(..., "--next-version", help="Explicit next SNAPSHOT version"),
    from_train: str = typer.Option(..., "--from-train", help="Completed development build train"),
    cee_version: str = typer.Option(..., "--cee-version", help="Exact public npmjs CEE version"),
    accept_red_develop: list[str] = typer.Option(
        None, "--accept-red-develop", help=ACCEPT_RED_DEVELOP_HELP),
    unattended: bool = typer.Option(
        False, "--unattended", help=UNATTENDED_HELP),
):
    """Run a manifest-owned train release through verified Git and publication stages."""
    manifest = _build_or_exit(release_version, next_version, from_train, cee_version)
    _render_plan(manifest)
    _preflight_or_exit(manifest, _parse_accepted_red_develop(accept_red_develop))
    state = ReleaseState()
    try:
        path = state.start(manifest)
        active = _drive_release(state, unattended=unattended)
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        if state.current_path.exists():
            console.print("Release state and the failed attempt were retained; use cedarcli release resume.")
        raise typer.Exit(1) from error
    console.print(f"Phase:               {active['phase']}")
    console.print(f"Internal state:      {path}")


@app.command("resume")
def resume(
    unattended: bool = typer.Option(False, "--unattended", help=UNATTENDED_HELP),
):
    """Resume the active train-backed release from its recorded phase."""
    state = ReleaseState()
    try:
        _, path = state.read_current_manifest()
        manifest = _drive_release(state, unattended=unattended)
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    _render_plan(manifest)
    console.print(f"Phase:               {manifest['phase']}")
    console.print(f"Internal state:      {path}")


def _status_summary(manifest: dict, path: Path) -> dict:
    """Reduce the ledger to the fields a monitor polls, leaving evidence in the ledger."""
    return {
        "release": manifest.get("releaseVersion"),
        "nextDevelopment": manifest.get("nextDevelopmentVersion"),
        "train": manifest.get("train"),
        "phase": manifest.get("phase"),
        "complete": manifest.get("phase") == "accepted",
        "failure": manifest.get("failure"),
        "lastAttempt": manifest.get("lastAttempt"),
        "localRefs": len(manifest.get("localRefs", {}).get("completedTasks", {})),
        "remoteIntegration": len(
            manifest.get("remoteIntegration", {}).get("completedTasks", {})),
        "snapshotPublication": len(
            manifest.get("snapshotPublication", {}).get("completedTasks", {})),
        "artifactPublication": len(
            manifest.get("artifactPublication", {}).get("completedTasks", {})),
        "acceptance": manifest.get("acceptance", {}).get("checks", []),
        "statePath": str(path),
    }


@app.command("conclude")
def conclude():
    """Release the active slot held by a release that has already reached its end."""
    state = ReleaseState()
    try:
        manifest, path = state.read_current_manifest()
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    phase = manifest.get("phase")
    # A release that predates the acceptance phase ended at artifacts-published, and that was
    # terminal under the layout that wrote it. Both count as finished; nothing else does.
    if phase not in {RELEASE_TERMINAL_PHASE, "artifacts-published"}:
        console.print(
            f"[red]release {manifest.get('releaseVersion')} is {phase} and has not finished; "
            "use cedarcli release resume[/red]"
        )
        raise typer.Exit(1)
    state.conclude()
    console.print(f"Concluded:           {manifest.get('releaseVersion')} at {phase}")
    console.print(f"Record retained:     {path}")


@app.command("status")
def status(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the release state as JSON for monitoring"),
):
    """Show the active train-backed release and its immutable CEE proof."""
    try:
        manifest, path = ReleaseState().read_current_manifest()
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    if as_json:
        print(json.dumps(_status_summary(manifest, path), indent=2))
        return
    _render_plan(manifest)
    console.print(f"Phase:               {manifest['phase']}")
    if manifest.get("lastAttempt"):
        console.print(f"Last attempt:        {manifest['lastAttempt']}")
    if manifest.get("failure"):
        console.print(f"Failure:             {manifest['failure']}")
    if manifest.get("localRefs"):
        completed = manifest["localRefs"].get("completedTasks", {})
        console.print(f"Local refs:          {len(completed)} prepared refs")
    if manifest.get("remoteIntegration"):
        completed = manifest["remoteIntegration"].get("completedTasks", {})
        console.print(f"Remote integration:  {len(completed)} repositories")
    if manifest.get("snapshotPublication"):
        completed = manifest["snapshotPublication"].get("completedTasks", {})
        console.print(f"Snapshot publication:{len(completed):>3} verified tasks")
    if manifest.get("artifactPublication"):
        completed = manifest["artifactPublication"].get("completedTasks", {})
        console.print(f"Artifact publication:{len(completed):>3} verified tasks")
    if manifest.get("acceptance"):
        for check in manifest["acceptance"]["checks"]:
            console.print(f"Accepted:            {check['detail']}")
    console.print(f"Internal state:      {path}")

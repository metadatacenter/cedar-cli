"""Manifest-backed CEDAR releases sourced from immutable build trains.

This module is deliberately independent of the legacy release planners.  The
new commands may coexist with them until the train-backed release is complete.
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
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
FRONTEND_BUILD_SURFACES = [
    {"id": "template-editor", "repository": "cedar-template-editor", "directory": ".",
     "install": [], "build": []},
    {"id": "workspace", "repository": "cedar-workspace", "directory": ".",
     "install": [], "build": []},
    {"id": "openview", "repository": "cedar-openview", "directory": "cedar-openview-src",
     "install": ["--legacy-peer-deps"], "build": ["npm", "run", "build", "--", "--configuration=production"]},
    {"id": "bridging", "repository": "cedar-bridging", "directory": "cedar-bridging-src",
     "install": [], "build": ["npm", "run", "build", "--", "--configuration=production"]},
    {"id": "monitoring", "repository": "cedar-monitoring", "directory": "cedar-monitoring-src",
     "install": [], "build": ["npm", "run", "build", "--", "--configuration=production"]},
    {"id": "content", "repository": "cedar-content-distribution", "directory": ".",
     "install": [], "build": []},
    {"id": "cee-demo-angular", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-angular-src", "install": ["--legacy-peer-deps"],
     "build": ["npm", "run", "build", "--", "--configuration=production"]},
    {"id": "cee-demo-ember", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-ember-src", "install": [],
     "build": ["npm", "run", "build"]},
    {"id": "cee-demo-react", "repository": "cedar-component-demo",
     "directory": "cedar-cee-demo-react", "install": [],
     "build": ["npm", "run", "build"]},
]


class ReleaseError(RuntimeError):
    """A release input or immutable artifact failed validation."""


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
            raise ReleaseError(f"cannot read {url}: {error}") from error

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


def compare_cee_packages(
    dev_tarball: bytes,
    dev_version: str,
    public_tarball: bytes,
    public_version: str,
) -> dict:
    """Prove that a public CEE package is a metadata-only promotion of a train package."""

    if dev_version.split("-dev.", 1)[0] != public_version:
        raise ReleaseError(
            f"train CEE {dev_version} cannot be promoted as public CEE {public_version}"
        )
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
        "allowedMetadataChanges": [
            "package.json:name",
            "package.json:version",
            "package.json:publishConfig",
            "package-lock.json:name",
            "package-lock.json:version",
            "package-lock.json:packages['']:name",
            "package-lock.json:packages['']:version",
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
        consumers = self._cee_consumers(frontend_config, npm_plan, source)

        planned_cee = npm_plan.get("cee", {})
        dev_version = planned_cee.get("version")
        if planned_cee.get("name") != DEV_CEE_NAME or not isinstance(dev_version, str):
            raise ReleaseError(f"npm plan has no {DEV_CEE_NAME} development package")
        if dev_version.split("-dev.", 1)[0] != cee_version:
            raise ReleaseError(
                f"explicit CEE {cee_version} does not promote train CEE {dev_version}"
            )
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
            raise ReleaseError(
                f"release {current['releaseVersion']} is already active; use cedarcli release status"
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
    def _stamp_repository(
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

        variants = {}
        for variant, workspace, target in (
            ("release", release_workspace, release_version),
            ("nextDevelopment", next_workspace, next_version),
        ):
            records = {}
            for repository in release_repositories:
                root = workspace / repository
                stamped = self._stamp_repository(
                    repository, root, old, target, maven_repositories
                )
                allowed = set(stamped)
                if variant == "release":
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
                    tasks.append({
                        "id": self._task_id(variant, "npm", surface["id"], "build"),
                        "variant": variant,
                        "kind": "frontend-build",
                        "repository": surface["repository"],
                        "cwd": str(root),
                        "command": surface["build"],
                        "tests": False,
                    })
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
        return {
            **task,
            "startedAt": started,
            "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "log": str(log),
            "logSha256": _file_sha256(log),
        }

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


def advance_active_release(
    state: ReleaseState | None = None,
    workspace_preparer: ReleaseWorkspacePreparer | None = None,
    version_preparer: ReleaseVersionPreparer | None = None,
    build_validator: ReleaseBuildValidator | None = None,
    ref_creator: ReleaseRefCreator | None = None,
) -> dict:
    state = state or ReleaseState()
    manifest, _ = state.read_current_manifest()
    if manifest.get("phase") == "local-refs-created":
        return manifest
    if manifest.get("phase") in {"creating-local-refs", "local-ref-creation-failed"}:
        return create_active_release_refs(state, ref_creator)
    if manifest.get("phase") in {
        "builds-validated",
    }:
        return create_active_release_refs(state, ref_creator)
    if manifest.get("phase") in {
        "versions-prepared", "validating-builds", "build-validation-failed",
    }:
        validate_active_release_builds(state, build_validator)
        return create_active_release_refs(state, ref_creator)
    if manifest.get("phase") == "version-preparation-failed":
        # A partial stamping attempt remains as evidence. Re-run the frontend stage into a new
        # attempt so resume never needs a destructive reset of CLI-owned release state.
        state.update_current_manifest({"phase": "frontend-preparation-failed"})
    prepare_active_release(state, workspace_preparer)
    prepare_active_release_versions(state, version_preparer)
    validate_active_release_builds(state, build_validator)
    return create_active_release_refs(state, ref_creator)


def _render_plan(manifest: dict) -> None:
    cee = manifest["cee"]
    console.print(f"Release:             {manifest['releaseVersion']}")
    console.print(f"Next development:    {manifest['nextDevelopmentVersion']}")
    console.print(f"Source train:        {manifest['train']}")
    console.print(
        "CEE promotion:       "
        f"{cee['development']['version']} -> {cee['public']['version']}"
    )
    console.print(f"CEE payload SHA-256: {cee['promotionProof']['normalizedPayloadSha256']}")
    console.print("CEE package content: identical after the declared npm channel metadata change")


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


@app.command("plan")
def plan(
    release_version: str = typer.Option(..., "--version", help="Explicit CEDAR release version"),
    next_version: str = typer.Option(..., "--next-version", help="Explicit next SNAPSHOT version"),
    from_train: str = typer.Option(..., "--from-train", help="Completed development build train"),
    cee_version: str = typer.Option(..., "--cee-version", help="Exact public npmjs CEE version"),
):
    """Validate explicit release inputs without changing release state."""
    manifest = _build_or_exit(release_version, next_version, from_train, cee_version)
    _render_plan(manifest)
    console.print("No changes made.")


@app.command("start")
def start(
    release_version: str = typer.Option(..., "--version", help="Explicit CEDAR release version"),
    next_version: str = typer.Option(..., "--next-version", help="Explicit next SNAPSHOT version"),
    from_train: str = typer.Option(..., "--from-train", help="Completed development build train"),
    cee_version: str = typer.Option(..., "--cee-version", help="Exact public npmjs CEE version"),
):
    """Prepare, validate, and commit isolated release sources to local refs."""
    manifest = _build_or_exit(release_version, next_version, from_train, cee_version)
    state = ReleaseState()
    try:
        path = state.start(manifest)
        active = advance_active_release(state)
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        if state.current_path.exists():
            console.print("Release state and the failed attempt were retained; use cedarcli release resume.")
        raise typer.Exit(1) from error
    _render_plan(active)
    console.print("Local release branches and tags created; nothing was pushed.")
    console.print(f"Internal state:      {path}")


@app.command("resume")
def resume():
    """Resume the active train-backed release from its recorded phase."""
    state = ReleaseState()
    try:
        manifest = advance_active_release(state)
        _, path = state.read_current_manifest()
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    _render_plan(manifest)
    console.print(f"Phase:               {manifest['phase']}")
    console.print(f"Internal state:      {path}")


@app.command("status")
def status():
    """Show the active train-backed release and its immutable CEE proof."""
    try:
        manifest, path = ReleaseState().read_current_manifest()
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    _render_plan(manifest)
    console.print(f"Phase:               {manifest['phase']}")
    if manifest.get("lastAttempt"):
        console.print(f"Last attempt:        {manifest['lastAttempt']}")
    if manifest.get("failure"):
        console.print(f"Failure:             {manifest['failure']}")
    if manifest.get("localRefs"):
        completed = manifest["localRefs"].get("completedTasks", {})
        console.print(f"Local refs:          {len(completed)} repositories; pushed: no")
    console.print(f"Internal state:      {path}")

"""CEDAR release packages."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import base64
import copy
import hashlib
import io
import json
import re
import subprocess
import tarfile
import urllib.parse
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _json_bytes,
    _sha256,
)
from org.metadatacenter.release_support.policy import (
    CEE_BUNDLE_PAIRS,
    DEV_CEE_NAME,
    DEV_MODEL_SPEC_RE,
    LOAD_TRACE_RE,
    PUBLIC_CEE_NAME,
    REQUIRED_CEE_FILES,
    NEXUS_NPM_REGISTRY,
    PUBLIC_NPM_REGISTRY,
)


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


def _tarball_files(identity: str, content: bytes, *, required_files=None) -> dict[str, bytes]:
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
    missing = (REQUIRED_CEE_FILES if required_files is None else required_files) - files.keys()
    if missing:
        raise ReleaseError(f"{identity} is missing required files: {', '.join(sorted(missing))}")
    return files


def load_model_runtime(http, version: str) -> bytes:
    """Fetch the exact model package whose bytes an integration manifest records."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-dev\.[0-9A-Za-z.-]+)?", version):
        raise ReleaseError("CEE integration has an invalid model version")
    development = "-dev." in version
    name = ("@org.metadatacenter/" if development else "") + "cedar-model-typescript-library"
    registry = NEXUS_NPM_REGISTRY if development else PUBLIC_NPM_REGISTRY
    result = http.read_json(registry.rstrip("/") + "/" + urllib.parse.quote(name, safe="") + "/" + version)
    if result is None:
        raise ReleaseError(f"Missing integration model {name}@{version}")
    record = result[0]
    if record.get("name") != name or record.get("version") != version:
        raise ReleaseError(f"Registry returned a different integration model for {name}@{version}")
    payload = http.read(record["dist"]["tarball"])
    _verify_integrity(f"{name}@{version}", payload, record["dist"]["integrity"])
    return payload


def _model_runtime_fingerprints(content: bytes, version: str) -> tuple[str, str]:
    files = _tarball_files("integration model", content, required_files={"package.json"})
    metadata = _read_package_json("integration model", files, "package.json")
    name = ("@org.metadatacenter/" if "-dev." in version else "") + "cedar-model-typescript-library"
    if metadata.get("version") != version or metadata.get("name") != name:
        raise ReleaseError("Integration model tarball has a different package identity")
    normalized = {key: value for key, value in metadata.items() if key not in {"name", "version", "publishConfig"}}
    module = "esm" if any(path.startswith("esm/") for path in files) else metadata.get("module")
    if not isinstance(module, str) or module.startswith("/") or ".." in PurePosixPath(module).parts:
        raise ReleaseError("Integration model has no safe ESM entry")
    def fingerprint(normalize):
        digest = hashlib.sha256()
        def visit(path):
            digest.update(path.encode() + b"\0")
            if path in files:
                value = (json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                         if normalize and path == "package.json" else files[path])
                digest.update(value)
            else:
                children = sorted({name[len(path) + 1:].split("/")[0] for name in files if name.startswith(path + "/")})
                if not children:
                    raise ReleaseError(f"Integration model is missing {path}")
                for child in children:
                    visit(path + "/" + child)
            digest.update(b"\0")
        visit("package.json")
        visit(module)
        return digest.hexdigest()
    return fingerprint(False), fingerprint(True)


def _integration_manifests(dev_files, public_files, public_version, model_runtime_loader,
                           label="CEE"):
    """Prove external model bytes as well as the integration bundle itself."""
    if "integration-manifest.json" not in dev_files:
        return None
    if model_runtime_loader is None:
        raise ReleaseError(f"{label} integration promotion requires verified model runtime packages")
    _, public_model = _public_release_changelog(f"public {label}", public_files["CHANGELOG.md"], public_version)
    normalized = []
    loaded = {}
    for side, files in (("development", dev_files), ("public", public_files)):
        manifest = _read_package_json(side, files, "integration-manifest.json")
        if (set(manifest) != {"format", "version", "source", "bytes", "sha256", "runtimes"}
                or manifest.get("format") != "cedar-integration" or manifest.get("version") != 1
                or not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("source", "")))
                or not isinstance(manifest.get("runtimes"), dict)):
            raise ReleaseError(f"{side} {label} has malformed integration provenance")
        model = manifest["runtimes"].get("cedar-model-typescript-library")
        if not isinstance(model, dict) or set(model) != {"version", "sha256", "runtimeSha256"}:
            raise ReleaseError(f"{side} {label} integration lacks a complete model runtime fingerprint")
        version = model.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-dev\.[0-9A-Za-z.-]+)?", version):
            raise ReleaseError(f"{side} {label} integration has an invalid model version")
        if side == "public" and version != public_model:
            raise ReleaseError(f"Public {label} integration model differs from its changelog")
        if version not in loaded:
            loaded[version] = _model_runtime_fingerprints(model_runtime_loader(version), version)
        if (model.get("sha256"), model.get("runtimeSha256")) != loaded[version]:
            raise ReleaseError(f"{side} {label} integration model fingerprint does not match its published package")
        for name, runtime in manifest["runtimes"].items():
            if name != "cedar-model-typescript-library" and (not isinstance(runtime, dict) or set(runtime) != {"version"}):
                raise ReleaseError(f"{side} {label} integration has malformed runtime {name}")
        manifest["source"] = "<source-provenance>"
        model["version"] = "<model-package-version>"
        model["sha256"] = "<model-package-provenance>"
        normalized.append(manifest)
    if normalized[0]["runtimes"] != normalized[1]["runtimes"]:
        raise ReleaseError(f"{label} promotion changes integration runtime code or dependencies")
    return normalized


def _normalize_license_identity(normalized_dev: dict, normalized_public: dict) -> bool:
    """Give the development notice the public model's heading, when that is all that differs.

    Call this only after proving the exact published model runtimes equivalent: only then may
    their channel-specific notice headings differ. Every other license byte remains compared.
    """
    notice = "integration-licenses.txt"
    if normalized_dev.get(notice) == normalized_public.get(notice):
        return False
    dev_heading = b"Package: @org.metadatacenter/cedar-model-typescript-library\n"
    public_heading = b"Package: cedar-model-typescript-library\n"
    dev_lines = normalized_dev.get(notice, b"").splitlines(keepends=True)
    public_lines = normalized_public.get(notice, b"").splitlines(keepends=True)
    if (dev_lines.count(dev_heading) == 1 and public_lines.count(public_heading) == 1
            and public_heading not in dev_lines and dev_heading not in public_lines):
        dev_lines[dev_lines.index(dev_heading)] = public_heading
        normalized_dev[notice] = b"".join(dev_lines)
        return True
    return False


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


def _bundle_pairs(files: dict[str, bytes], pairs=CEE_BUNDLE_PAIRS) -> list[tuple[str, str]]:
    """The bundle/manifest pairs this package carries, the first one always."""
    required, *optional = pairs
    return [required] + [
        pair for pair in optional if pair[0] in files or pair[1] in files
    ]


def _verify_bundle(identity: str, files: dict[str, bytes], pairs=CEE_BUNDLE_PAIRS) -> None:
    for bundle_name, manifest_name in _bundle_pairs(files, pairs):
        if bundle_name not in files:
            raise ReleaseError(f"{identity} has {manifest_name} without {bundle_name}")
        manifest = _read_package_json(identity, files, manifest_name)
        bundle = files[bundle_name]
        if manifest.get("bytes") != len(bundle) or manifest.get("sha256") != _sha256(bundle):
            raise ReleaseError(f"{identity} JavaScript does not match {manifest_name}")


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
    development_allow_scripts: dict[str, bool] | None = None,
    development_design_tokens_spec: str | None = None,
) -> tuple[bytes, bytes, list[str]]:
    """Normalize provenance and captured build-only install policy, never executable code.

    Returns both bundles and the labels of the normalizations applied. Until CEE 2.0.30 the
    bundle embedded CEE's whole package.json, and with it the design-token pin, the install
    policy and the model package identity. A bundle that embeds only its version carries none of
    the three, so each is normalized only where the bundles carry it. Skipping one leaves more
    bytes to compare, never fewer.
    """

    normalized_dev = dev_bundle
    normalized_public = public_bundle
    applied = []
    if development_design_tokens_spec is not None:
        # Normalize only this one build-time pin, inside an embedded build manifest's
        # devDependencies; compiled styles still compare.
        pin = rb'(?:npm:@org\.metadatacenter/cedar-design-tokens@)?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?'
        if not re.fullmatch(pin, development_design_tokens_spec.encode()):
            raise ReleaseError('Train design-token dependency is not an exact package pin')
        field = rb'("@org\.metadatacenter/cedar-design-tokens":"' + pin + rb'")'
        pattern = re.compile(rb'(?:devDependencies|"devDependencies"):\{[^{}]*?' + field)
        # A bundle on either side that carries the pin requires exactly one on both.
        embedded = bool(pattern.search(normalized_dev) or pattern.search(normalized_public))
        if embedded:
            applied.append("embedded design-token development pin")
        for identity, bundle, expected in (
            (dev_identity, normalized_dev, development_design_tokens_spec),
            (public_identity, normalized_public, None),
        ) if embedded else ():
            value = _one_match(identity, 'embedded design-token development pin', pattern, bundle)
            if expected is not None and value != (
                b'"@org.metadatacenter/cedar-design-tokens":"' + expected.encode() + b'"'
            ):
                raise ReleaseError('CEE design-token pin disagrees with the train plan')
            normalized = _replace_once(identity, 'embedded design-token development pin', bundle,
                                       value, b'"@org.metadatacenter/cedar-design-tokens":"<design-token-pin>"')
            if expected is not None:
                normalized_dev = normalized
            else:
                normalized_public = normalized
    if development_allow_scripts:
        if not all(
            isinstance(package, str) and package and allowed is True
            for package, allowed in development_allow_scripts.items()
        ):
            raise ReleaseError(
                f"{dev_identity} source allowScripts must contain only non-empty package names "
                "explicitly set to true"
            )
        policy = (
            b",allowScripts:"
            + json.dumps(
                development_allow_scripts,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        public_count = normalized_public.count(policy)
        if policy in normalized_dev or public_count:
            applied.append("embedded allowScripts install policy")
            normalized_dev = _replace_once(
                dev_identity,
                "embedded allowScripts install policy",
                normalized_dev,
                policy,
                b"",
            )
            if public_count > 1:
                raise ReleaseError(
                    f"{public_identity} bundle must contain at most one embedded allowScripts "
                    f"install policy; found {public_count}"
                )
            if public_count == 1:
                normalized_public = normalized_public.replace(policy, b"", 1)
    substitutions = [
        (
            "CEE version",
            dev_version.encode(),
            public_version.encode(),
            b"<cee-version>",
        ),
        (
            "load trace",
            _one_match(dev_identity, "load trace", LOAD_TRACE_RE, dev_bundle),
            _one_match(public_identity, "load trace", LOAD_TRACE_RE, public_bundle),
            b"<cee-load-trace>",
        ),
    ]
    # The model package is named only by an embedded build manifest. Without one, the public
    # model version is left in place, so a bundle that names it anyway fails the comparison.
    if DEV_MODEL_SPEC_RE.search(dev_bundle):
        substitutions.insert(1, (
            "model package identity",
            _one_match(dev_identity, "development model package identity", DEV_MODEL_SPEC_RE,
                       dev_bundle),
            public_model_version.encode(),
            b"<model-package-identity>",
        ))
    for label, dev_value, public_value, placeholder in substitutions:
        applied.append(label)
        normalized_dev = _replace_once(
            dev_identity, label, normalized_dev, dev_value, placeholder,
        )
        normalized_public = _replace_once(
            public_identity, label, normalized_public, public_value, placeholder,
        )
    return normalized_dev, normalized_public, applied


def _canonicalize_minified_renames(
    dev_identity: str,
    dev_bundle: bytes,
    public_identity: str,
    public_bundle: bytes,
    bundle_name: str = "cedar-embeddable-editor.js",
    label: str = "CEE",
) -> tuple[bytes, int]:
    """Prove alpha-equivalence with lexical binding identities, preserving every other byte.

    Independent scopes can reuse a short name or rename it differently. A global spelling map
    cannot prove those bundles equivalent. The parser resolves every declaration/reference and
    keeps properties, public exports, globals, strings, regexes and punctuation unchanged.
    """
    refusal = ReleaseError(
        f"{label} promotion changes executable JavaScript in {bundle_name} outside declared "
        "release provenance"
    )
    directory = Path(__file__).resolve().parents[3] / "scripts" / "javascript"
    dependencies = json.loads((directory / "package.json").read_text())["dependencies"]
    ready = True
    for name, version in dependencies.items():
        try:
            installed = json.loads((directory / "node_modules" / name / "package.json").read_text())
            ready = ready and installed.get("version") == version
        except (OSError, ValueError):
            ready = False
    if not ready:
        setup = subprocess.run(
            ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=directory, capture_output=True, text=True,
        )
        if setup.returncode:
            raise ReleaseError("Could not install pinned JavaScript release verification tools")
    try:
        result = subprocess.run(
            ["node", str(directory / "compare-scoped-bundles.cjs")],
            input=json.dumps({"development": dev_bundle.decode(), "public": public_bundle.decode()}),
            capture_output=True, text=True, timeout=60,
        )
        proof = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise ReleaseError("Could not run scoped JavaScript release verification") from error
    if proof.get("equivalent") is not True or not isinstance(proof.get("renames"), int):
        raise refusal
    return public_bundle, proof["renames"]


def _normalized_bundle_manifest(bundle: bytes) -> bytes:
    return _json_bytes({"bytes": len(bundle), "sha256": _sha256(bundle)})


def compare_cee_packages(
    dev_tarball: bytes,
    dev_version: str,
    public_tarball: bytes,
    public_version: str,
    *,
    development_allow_scripts: dict[str, bool] | None = None,
    development_design_tokens_spec: str | None = None,
    development_bundle_version: str | None = None,
    model_runtime_loader=None,
) -> dict:
    """Prove that a public CEE package is a metadata-only promotion of a train package.

    A train stamps its version before it builds, so its bundle reports the package's version.
    CEE's own CI publishes a commit-derived version for a bundle built under the committed one;
    `development_bundle_version` names what such a bundle reports, when it differs.
    """

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
    integration_manifests = _integration_manifests(
        dev_files, public_files, public_version, model_runtime_loader,
    )
    license_identity_changed = bool(integration_manifests) and _normalize_license_identity(
        normalized_dev, normalized_public)
    minified_renames = 0
    # The normalizations each bundle needed, by bundle name; a bundle identical in both packages
    # needed none.
    applied_by_bundle: dict[str, list[str]] = {}
    changed = [
        name for name in sorted(normalized_dev)
        if normalized_dev[name] != normalized_public[name]
    ]
    if changed:
        pairs = _bundle_pairs(normalized_dev)
        allowed = {"CHANGELOG.md"}
        for bundle_name, manifest_name in pairs:
            allowed.update({bundle_name, manifest_name})
        unexpected = sorted(set(changed) - allowed)
        if unexpected:
            raise ReleaseError(
                "CEE promotion changes package content outside allowed channel metadata: "
                + ", ".join(unexpected)
            )
        changed_set = set(changed)
        # A bundle and its manifest move together, because the manifest records the bytes. One
        # without the other means the manifest no longer describes what it names.
        for bundle_name, manifest_name in pairs:
            if bundle_name != "integration.mjs" and (bundle_name in changed_set) != (manifest_name in changed_set):
                raise ReleaseError(
                    "CEE promotion changes an incomplete bundle-provenance pair: "
                    + ", ".join(changed)
                )
        changed_pairs = [
            pair for pair in pairs if pair[0] in changed_set or pair[1] in changed_set
        ]
        bundle_changed = bool(changed_pairs)

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
            # Each bundle carries the same provenance strings, so each is proved the same way.
            for bundle_name, manifest_name in changed_pairs:
                dev_bundle, public_bundle, applied_by_bundle[bundle_name] = _normalize_bundle_provenance(
                    dev_identity,
                    dev_files[bundle_name],
                    development_bundle_version or dev_version,
                    public_identity,
                    public_files[bundle_name],
                    public_version,
                    public_model_version,
                    development_allow_scripts,
                    development_design_tokens_spec,
                )
                if dev_bundle != public_bundle:
                    dev_bundle, renames = _canonicalize_minified_renames(
                        dev_identity, dev_bundle, public_identity, public_bundle,
                        bundle_name,
                    )
                    minified_renames += renames
                normalized_dev[bundle_name] = dev_bundle
                normalized_public[bundle_name] = public_bundle
                normalized_dev[manifest_name] = _normalized_bundle_manifest(dev_bundle)
                normalized_public[manifest_name] = _normalized_bundle_manifest(public_bundle)
                if bundle_name == "integration.mjs":
                    for target, manifest, bundle in (
                        (normalized_dev, integration_manifests[0], dev_bundle),
                        (normalized_public, integration_manifests[1], public_bundle),
                    ):
                        target[manifest_name] = _json_bytes({**manifest, "bytes": len(bundle), "sha256": _sha256(bundle)})
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
    pairs = _bundle_pairs(normalized_dev)
    bundle_changes = []
    if license_identity_changed:
        bundle_changes.append("integration-licenses.txt:verified model package identity")
    for bundle_name, manifest_name in pairs:
        applied = applied_by_bundle.get(bundle_name, [])
        bundle_changes.extend(
            f"{bundle_name}:{label}"
            for label in (
                "CEE version",
                "model package identity",
                "load trace",
            )
            if label in applied
        )
        bundle_changes.append(f"{manifest_name}:derived bundle bytes and sha256")
        if bundle_name == "integration.mjs":
            bundle_changes.append("integration-manifest.json:source and verified model package provenance")
        bundle_changes.extend(
            f"{bundle_name}:{label}"
            for label in (
                "embedded allowScripts install policy",
                "embedded design-token development pin",
            )
            if label in applied
        )
        if minified_renames:
            bundle_changes.append(f"{bundle_name}:minified identifier names")
    return {
        "algorithm": "sha256",
        "normalizedPayloadSha256": digest,
        "fileCount": len(normalized_dev),
        "bundleSha256": _sha256(dev_files["cedar-embeddable-editor.js"]),
        "publicBundleSha256": _sha256(public_files["cedar-embeddable-editor.js"]),
        "normalizedBundleSha256": _sha256(normalized_dev["cedar-embeddable-editor.js"]),
        # Every bundle the package ships, so the evidence names what was proved rather than
        # leaving a second one to be assumed from the first.
        "provenBundles": {
            bundle_name: {
                "bundleSha256": _sha256(dev_files[bundle_name]),
                "publicBundleSha256": _sha256(public_files[bundle_name]),
                "normalizedBundleSha256": _sha256(normalized_dev[bundle_name]),
            }
            for bundle_name, _ in pairs
        },
        "minifiedIdentifierRenames": minified_renames,
        "allowedMetadataChanges": [
            "package.json:name",
            "package.json:version",
            "package.json:publishConfig",
            "package-lock.json:name",
            "package-lock.json:version",
            "package-lock.json:packages['']:name",
            "package-lock.json:packages['']:version",
        ] + bundle_changes + [
            f"CHANGELOG.md:{public_version} release entry",
        ],
    }


def _normalized_component_manifest(manifest: dict, bundle: bytes) -> dict:
    """A bundle manifest describing `bundle`, with its build fingerprints set aside.

    A component's manifest records a fingerprint of the sources a build read, which covers its
    package.json and the installed model package, and one of the build's output. Both change with
    the channel even when no executable byte does. Every other field is still compared.
    """
    normalized = {**manifest, "bytes": len(bundle), "sha256": _sha256(bundle)}
    if "provenance" in normalized:
        normalized["provenance"] = "<build-provenance>"
    return normalized


def compare_component_packages(
    component,
    dev_tarball: bytes,
    dev_version: str,
    public_tarball: bytes,
    public_version: str,
    *,
    model_runtime_loader=None,
) -> dict:
    """Prove that a public CED or CETP package is a metadata-only promotion of a train package.

    The train stamps its version before it builds, so a bundle that reports its version reports
    the train's, as one quoted literal. The proof is directional: replacing that one literal in the
    train's bundle with the public version must reproduce the public bundle. A short public
    version such as 0.1.0 also occurs inside unrelated strings, so the proof never searches the
    public bundle for it. A bundle that reports no version, as CETP's does not, must be identical.
    """

    label = component.label
    pairs = component.bundle_pairs
    dev_identity = f"{component.development_name}@{dev_version}"
    public_identity = f"{component.public_name}@{public_version}"
    dev_files = _tarball_files(dev_identity, dev_tarball, required_files=component.required_files)
    public_files = _tarball_files(
        public_identity, public_tarball, required_files=component.required_files)
    _verify_bundle(dev_identity, dev_files, pairs)
    _verify_bundle(public_identity, public_files, pairs)
    normalized_dev = _normalize_package_metadata(
        dev_identity, dev_files, expected_name=component.development_name,
        expected_version=dev_version, development=True)
    normalized_public = _normalize_package_metadata(
        public_identity, public_files, expected_name=component.public_name,
        expected_version=public_version, development=False)
    if normalized_dev.keys() != normalized_public.keys():
        only_dev = sorted(normalized_dev.keys() - normalized_public.keys())
        only_public = sorted(normalized_public.keys() - normalized_dev.keys())
        raise ReleaseError(
            f"{label} promotion changes the package file set: "
            f"development-only={only_dev}, public-only={only_public}")
    changes = [
        "package.json:name",
        "package.json:version",
        "package.json:publishConfig",
    ]

    # The public package carries one entry the train's may lack. A train that captured develop
    # after the release has the entry too, and then the two changelogs are identical.
    changelog, _ = _public_release_changelog(
        public_identity, public_files["CHANGELOG.md"], public_version)
    if normalized_dev["CHANGELOG.md"] == changelog:
        changes.append(f"CHANGELOG.md:{public_version} release entry")
    elif normalized_dev["CHANGELOG.md"] != public_files["CHANGELOG.md"]:
        raise ReleaseError(
            f"{label} promotion changes CHANGELOG.md outside the one current-release entry")
    normalized_dev["CHANGELOG.md"] = normalized_public["CHANGELOG.md"] = changelog

    integration = _integration_manifests(
        dev_files, public_files, public_version, model_runtime_loader, label=label)
    if integration and _normalize_license_identity(normalized_dev, normalized_public):
        changes.append("integration-licenses.txt:verified model package identity")

    renames = 0
    proven = {}
    dev_literal = b'"' + dev_version.encode() + b'"'
    public_literal = b'"' + public_version.encode() + b'"'
    for bundle_name, manifest_name in _bundle_pairs(normalized_dev, pairs):
        dev_bundle = dev_files[bundle_name]
        public_bundle = public_files[bundle_name]
        promoted = dev_bundle
        if dev_bundle != public_bundle:
            count = dev_bundle.count(dev_literal)
            if count > 1:
                raise ReleaseError(
                    f"{dev_identity} {bundle_name} must report its version once; found {count}")
            if count == 1:
                promoted = dev_bundle.replace(dev_literal, public_literal, 1)
                changes.append(f"{bundle_name}:{label} version")
            if promoted != public_bundle:
                promoted, count = _canonicalize_minified_renames(
                    dev_identity, promoted, public_identity, public_bundle, bundle_name,
                    label=label)
                renames += count
                changes.append(f"{bundle_name}:minified identifier names")
        normalized_dev[bundle_name] = promoted
        normalized_public[bundle_name] = public_bundle
        if bundle_name == "integration.mjs" and integration:
            dev_manifest, public_manifest = integration
            changes.append(
                "integration-manifest.json:source and verified model package provenance")
        else:
            dev_manifest = _read_package_json(dev_identity, dev_files, manifest_name)
            public_manifest = _read_package_json(public_identity, public_files, manifest_name)
            if dev_manifest.get("provenance") != public_manifest.get("provenance"):
                changes.append(f"{manifest_name}:build input and output fingerprints")
        if dev_bundle != public_bundle:
            changes.append(f"{manifest_name}:derived bundle bytes and sha256")
        normalized_dev[manifest_name] = _json_bytes(
            _normalized_component_manifest(dev_manifest, promoted))
        normalized_public[manifest_name] = _json_bytes(
            _normalized_component_manifest(public_manifest, public_bundle))
        proven[bundle_name] = {
            "bundleSha256": _sha256(dev_bundle),
            "publicBundleSha256": _sha256(public_bundle),
            "normalizedBundleSha256": _sha256(promoted),
        }

    changed = [name for name in sorted(normalized_dev)
               if normalized_dev[name] != normalized_public[name]]
    if changed:
        raise ReleaseError(
            f"{label} promotion changes package content outside allowed channel metadata: "
            + ", ".join(changed))
    return {
        "algorithm": "sha256",
        "normalizedPayloadSha256": _tree_digest(normalized_dev),
        "fileCount": len(normalized_dev),
        "provenBundles": proven,
        "minifiedIdentifierRenames": renames,
        "allowedMetadataChanges": changes,
    }

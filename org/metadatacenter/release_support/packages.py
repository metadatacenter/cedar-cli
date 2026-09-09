"""CEDAR release packages."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import base64
import copy
import hashlib
import io
import json
import re
import tarfile
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _json_bytes,
    _sha256,
)
from org.metadatacenter.release_support.policy import (
    DEV_CEE_NAME,
    DEV_MODEL_SPEC_RE,
    LOAD_TRACE_RE,
    MINIFIED_NAME_RE,
    MINIFIED_TOKEN_SPLIT,
    PUBLIC_CEE_NAME,
    REQUIRED_CEE_FILES,
    RESERVED_SHORT_WORDS,
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
    development_allow_scripts: dict[str, bool] | None = None,
) -> tuple[bytes, bytes]:
    """Normalize provenance and captured build-only install policy, never executable code."""

    normalized_dev = dev_bundle
    normalized_public = public_bundle
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
        normalized_dev = _replace_once(
            dev_identity,
            "embedded allowScripts install policy",
            normalized_dev,
            policy,
            b"",
        )
        public_count = normalized_public.count(policy)
        if public_count > 1:
            raise ReleaseError(
                f"{public_identity} bundle must contain at most one embedded allowScripts "
                f"install policy; found {public_count}"
            )
        if public_count == 1:
            normalized_public = normalized_public.replace(policy, b"", 1)
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


def _canonicalize_minified_renames(
    dev_identity: str,
    dev_bundle: bytes,
    public_identity: str,
    public_bundle: bytes,
) -> tuple[bytes, int]:
    """Spell the development bundle with the public bundle's minified names, or refuse.

    esbuild draws short identifier names from an alphabet it orders by how often each character
    occurs in the output. The provenance strings a train stamps into a bundle change those counts,
    so two builds of the same code can differ in every name at one rank of that alphabet while
    agreeing everywhere else. This accepts exactly that: the two bundles must match byte for byte
    outside identifiers, and every identifier that differs must be a short minified name rather
    than a property or a reserved word, renamed the same way at every position where the bundles
    differ, in both directions. The same short string may still stand unchanged elsewhere, because
    it also occurs inside data such as regex classes, version strings and base64 text, and nothing
    short of parsing the bundle can tell those from code. Two kinds of source change therefore pass
    as renames: one that permutes short local names consistently and changes nothing else, and one
    that leaves a renamed name in place at a single site. Both bundles have passed the complete CEE
    gate before they meet here, which is the defence that remains.
    """
    refusal = ReleaseError(
        "CEE promotion changes executable JavaScript outside declared release provenance"
    )
    dev_parts = MINIFIED_TOKEN_SPLIT.split(dev_bundle)
    public_parts = MINIFIED_TOKEN_SPLIT.split(public_bundle)
    if len(dev_parts) != len(public_parts):
        raise refusal
    forward: dict[bytes, bytes] = {}
    backward: dict[bytes, bytes] = {}
    canonical = list(dev_parts)
    for index, (dev_part, public_part) in enumerate(zip(dev_parts, public_parts)):
        if index % 2 == 0:
            if dev_part != public_part:
                raise refusal
            continue
        if dev_part == public_part:
            continue
        if not (MINIFIED_NAME_RE.match(dev_part) and MINIFIED_NAME_RE.match(public_part)):
            raise refusal
        if dev_part in RESERVED_SHORT_WORDS or public_part in RESERVED_SHORT_WORDS:
            raise refusal
        preceding = dev_parts[index - 1]
        if preceding.endswith(b".") and not preceding.endswith(b"..."):
            raise refusal
        if forward.setdefault(dev_part, public_part) != public_part:
            raise refusal
        if backward.setdefault(public_part, dev_part) != dev_part:
            raise refusal
        canonical[index] = public_part
    return b"".join(canonical), len(forward)


def _normalized_bundle_manifest(bundle: bytes) -> bytes:
    return _json_bytes({"bytes": len(bundle), "sha256": _sha256(bundle)})


def compare_cee_packages(
    dev_tarball: bytes,
    dev_version: str,
    public_tarball: bytes,
    public_version: str,
    *,
    development_allow_scripts: dict[str, bool] | None = None,
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
    minified_renames = 0
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
                development_allow_scripts,
            )
            if dev_bundle != public_bundle:
                dev_bundle, minified_renames = _canonicalize_minified_renames(
                    dev_identity, dev_bundle, public_identity, public_bundle,
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
        "minifiedIdentifierRenames": minified_renames,
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
        ] + (
            ["cedar-embeddable-editor.js:embedded allowScripts install policy"]
            if development_allow_scripts else []
        ) + (
            ["cedar-embeddable-editor.js:minified identifier names"]
            if minified_renames else []
        ),
    }

"""Reuse a frontend repository's passing reactor result when nothing it reads has changed.

A full frontend reactor verifies every repository, and on the 16-core workstation that work is
CPU-bound: most of a run re-proves components nobody edited. So a task that passes leaves a
result record under `.reactor/results/`, named by a fingerprint of everything the task read. The
fingerprint covers the content of the repository's source tree, its exact commands, the package
each of its CEDAR dependencies resolved to in this build, the integration bundles its checks
loaded, the Node.js and npm versions, the platform, and the reactor's own tooling: cedar-cli's
source and the frontend inputs in cedar-development. A later task with the same fingerprint takes
the recorded package, if it publishes one, and passes without running its commands.

A consumer's fingerprint holds the digest of every package it resolved, so a producer that changed
produces a different package and every repository built on it runs again. The fingerprint leaves
out the worker budget, scratch paths, the time and the shell's environment. A record whose package
is no longer in the store, or no longer has its digest, is not a result, and the task runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import time
from pathlib import Path

from org.metadatacenter import reactor
from org.metadatacenter.reactor_evidence import source_identity
from org.metadatacenter.util.InvocationContext import invocation_environment

SCHEMA = 1
RESULTS = "results"


class ResultError(RuntimeError):
    """A fingerprint could not be computed, so nothing may be reused."""


def results_root(cedar_home) -> Path:
    return reactor.store_root(cedar_home) / RESULTS


def _version(command):
    return subprocess.check_output(command, env=invocation_environment(), text=True).strip()


def tooling(cedar_home) -> dict:
    """What the reactor itself brings to every task, computed once per build."""
    build = reactor._build(cedar_home)
    cached = getattr(build, "tooling", None)
    if cached is not None:
        return cached
    from org.metadatacenter.build import FRONTEND_DEVELOPMENT_INPUTS
    home = Path(cedar_home)
    development = home / "cedar-development"
    listed = subprocess.check_output(
        ["git", "-C", str(development), "ls-files", "-z", "--", *FRONTEND_DEVELOPMENT_INPUTS])
    digest = hashlib.sha256()
    for raw in sorted(set(listed.split(b"\0")) - {b""}):
        path = development / os.fsdecode(raw)
        digest.update(raw + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"deleted")
    value = {
        "cli": source_identity(home / "cedar-cli")["sourceSha256"],
        "development": digest.hexdigest(),
        "node": _version(["node", "--version"]),
        "npm": _version(["npm", "--version"]),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    if build is not None:
        build.tooling = value
    return value


def resolved_packages(build_root) -> dict:
    """The local package each manifest in the copy points at, by manifest, section and name."""
    root = Path(build_root)
    found = {}
    for directory, children, files in os.walk(root):
        children[:] = sorted(name for name in children if name not in reactor.SKIPPED_DIRECTORIES)
        if "package.json" not in files:
            continue
        manifest = Path(directory) / "package.json"
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(package, dict):
            continue
        relative = manifest.relative_to(root).as_posix()
        for section in reactor.DEPENDENCY_SECTIONS:
            declared = package.get(section)
            if not isinstance(declared, dict):
                continue
            for name, spec in sorted(declared.items()):
                if isinstance(spec, str) and spec.startswith("file:") and spec.endswith(".tgz"):
                    found[f"{relative}:{section}:{name}"] = Path(spec[len("file:"):]).stem
    return found


def fingerprint(repo, build_root, identity, commands, ready_after, checks, cedar_home):
    """The key a passing result is recorded under, and the inputs it was computed from."""
    try:
        inputs = {
            "schemaVersion": SCHEMA,
            "repository": repo.name,
            "publishes": repo.published_package_path or None,
            "source": identity["sourceSha256"],
            "commands": list(commands),
            "packageReadyAfter": ready_after,
            "resolved": resolved_packages(build_root),
            "integration": [{key: item[key] for key in ("variable", "package", "sha256", "bundleSha256")}
                            for item in checks],
            "tooling": tooling(cedar_home),
        }
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        raise ResultError(f"cannot fingerprint {repo.name}: {error}") from error
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    return key, inputs


def lookup(cedar_home, key, repo):
    """The passing result recorded under `key` for `repo`, or None when there is no usable one."""
    try:
        record = json.loads((results_root(cedar_home) / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(record, dict) or record.get("schemaVersion") != SCHEMA
            or record.get("fingerprint") != key or record.get("repository") != repo.name):
        return None
    package = record.get("package")
    if not repo.published_package_path:
        return record if package is None else None
    if not isinstance(package, str) or not re.fullmatch(r"[0-9a-f]{64}", package):
        return None
    artifact = reactor.store_root(cedar_home) / "artifacts" / f"{package}.tgz"
    try:
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != package:
            return None
    except OSError:
        return None
    return record


def save(cedar_home, key, inputs, package):
    """Record a passing result. Recording it again for the same inputs replaces it whole."""
    directory = results_root(cedar_home)
    directory.mkdir(parents=True, exist_ok=True)
    record = {"schemaVersion": SCHEMA, "fingerprint": key, "repository": inputs["repository"],
              "package": package, "passedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "inputs": inputs}
    descriptor, temporary = tempfile.mkstemp(prefix="result-", suffix=".json", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, directory / f"{key}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

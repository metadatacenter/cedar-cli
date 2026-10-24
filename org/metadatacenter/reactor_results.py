"""Reuse a frontend repository's passing reactor result when nothing it reads has changed.

A full frontend reactor verifies every repository, and on the 16-core workstation that work is
CPU-bound: most of a run re-proves components nobody edited. So a task that passes leaves a
result record under `.reactor/results/`, named by a fingerprint of everything the task read. The
fingerprint covers the content of the repository's source tree, its exact commands, the package
each of its CEDAR dependencies resolved to in this build, the integration bundles its checks
loaded, the Node.js and npm versions, the platform, and the reactor's own tooling: cedar-cli's
source and the frontend inputs in cedar-development. Authoring surfaces additionally fingerprint
the Java runtime and every JAR their conformance oracle resolves. A later task with the same fingerprint takes
the recorded package, if it publishes one, and passes without running its commands.

A consumer's fingerprint holds the digest of every package it resolved, so a producer that changed
produces a different package and every repository built on it runs again. The fingerprint leaves
out the worker budget, scratch paths and time; behavior-affecting environment is hashed. A record whose package
is no longer in the store, or no longer has its digest, is not a result, and the task runs.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
import json
import os
import platform
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from org.metadatacenter import reactor
from org.metadatacenter.reactor_evidence import source_identity
from org.metadatacenter.util.InvocationContext import invocation_environment

SCHEMA = 2
RESULTS = "results"


def frontend_cli_input(path):
    """Exclude CLI tests/docs and gates that always run AFTER component verification.

    Unknown files and all build/runtime code remain inputs, including untracked additions.
    In particular, this is not an allowlist that silently misses a new reactor module.
    """
    return not (path.startswith(("tests/", "docs/", "assets/docs/", ".github/"))
                or path in {"README.md", "AGENTS.md", "CLAUDE.md", "license.txt",
                            "org/metadatacenter/smoke_gate.py",
                            "org/metadatacenter/search_harness.py"})


class ResultError(RuntimeError):
    """A fingerprint could not be computed, so nothing may be reused."""


def results_root(cedar_home) -> Path:
    return reactor.store_root(cedar_home) / RESULTS


def _version(command):
    return subprocess.check_output(command, env=invocation_environment(), text=True).strip()


def browser_images(home):
    """Bind container-based checks to the installed image bytes, not just a mutable tag."""
    scripts = [*Path(home).glob('cedar-*/browser/*.sh'),
               *Path(home).glob('cedar-*/visual/*.sh')]
    tags = sorted({tag for script in scripts for tag in
                   re.findall(r'mcr\.microsoft\.com/playwright:[A-Za-z0-9_.-]+', script.read_text())})
    if not tags:
        return {}
    result = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', *tags],
                                     env=invocation_environment(), text=True, stderr=subprocess.PIPE, timeout=30)
    identifiers = result.splitlines()
    if len(identifiers) != len(tags) or not all(re.fullmatch(r'sha256:[a-f0-9]{64}', item) for item in identifiers):
        raise ResultError('Browser container identity is unavailable')
    return dict(zip(tags, identifiers))


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
        "cli": source_identity(home / "cedar-cli", include=frontend_cli_input)["sourceSha256"],
        "development": digest.hexdigest(),
        "node": _version(["node", "--version"]),
        "npm": _version(["npm", "--version"]),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "browserImages": browser_images(home),
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


def java_conformance_inputs(build_root) -> dict | None:
    """The actual oracle dependencies, only for surfaces declaring a Java conformance gate.

    Read the oracle POM from the selected model tarball before npm installs it. Hash every resolved
    classpath entry, in classpath order: snapshots or transitive changes must invalidate a previous
    green frontend result even when the frontend sources and meta-schemas did not change.
    """
    manifest = Path(build_root) / "package.json"
    if not manifest.is_file():
        return None
    package = json.loads(manifest.read_text(encoding="utf-8"))
    if package.get("cedarJavaConformance") is not True:
        return None
    spec = package.get("dependencies", {}).get("cedar-model-typescript-library", "")
    if not spec.startswith("file:") or not spec.endswith(".tgz"):
        raise ResultError("Java conformance requires the reactor's selected model package")
    with tarfile.open(spec[len("file:"):], "r:gz") as archive:
        member = archive.getmember("package/conformance/pom.xml")
        if not member.isfile():
            raise ResultError("Java conformance POM is not a regular file")
        pom = archive.extractfile(member).read()
    environment = invocation_environment()
    java = str(Path(environment["JAVA_HOME"]) / "bin" / "java") if environment.get("JAVA_HOME") else "java"
    version = subprocess.run([java, "-version"], env=environment, check=True,
                             capture_output=True, text=True, timeout=30)
    with tempfile.TemporaryDirectory(prefix="cedar-java-inputs-") as directory:
        root = Path(directory)
        (root / "pom.xml").write_bytes(pom)
        classpath = root / "classpath"
        subprocess.run(["mvn", "-q", "-f", str(root / "pom.xml"),
                        "org.apache.maven.plugins:maven-dependency-plugin:3.8.1:build-classpath",
                        f"-Dmdep.outputFile={classpath}"], env=environment, check=True,
                       capture_output=True, text=True, timeout=300)
        paths = [Path(value) for value in classpath.read_text().strip().split(os.pathsep) if value]
        if not paths:
            raise ResultError("Java conformance resolved an empty classpath")
        return {"java": (version.stdout + version.stderr).strip(),
                "pom": hashlib.sha256(pom).hexdigest(),
                "classpath": [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                              for path in paths]}


def environment_inputs():
    # Only behavior-affecting inputs; worker allocations and disposable paths are scheduling.
    excluded = {'CEDAR_HOME', 'CEDAR_CLI_CALLER_DIR', 'CEDAR_BUILD_WORKSPACE_ROOT', 'CEDAR_BUILD_DIAGNOSTIC_LOG',
                'CEDAR_BUILD_TMPDIR', 'CEDAR_TEST_WORKERS', 'CEDAR_WORKER_POOL',
                'CEDAR_TASK_CACHE_CONFIG', 'CEDAR_STAGE_CACHE', 'CEDAR_STAGE_CACHE_MODULE',
                'NG_BUILD_MAX_WORKERS', 'VITEST_MAX_WORKERS', 'npm_config_cache', 'npm_config_logs_dir'}
    return {key: hashlib.sha256(value.encode()).hexdigest()
            for key, value in invocation_environment().items()
            if (key.startswith(('NODE_', 'CEDAR_', 'CEF_', 'CED_', 'CEE_', 'WORKSPACE_',
                                'PLAYWRIGHT_', 'VITE_', 'NG_', 'npm_config_', 'NPM_CONFIG_'))
                or key in {'TZ', 'LANG', 'LC_ALL', 'CI', 'BROWSERSLIST_ENV', 'BROWSERSLIST', 'NODE_ENV'})
            and key not in excluded and not key.startswith('NODE_REPL_')}


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
            "environment": environment_inputs(),
            "trace": invocation_environment().get("CEDAR_TEST_TRACE", "retry"),
            "javaConformance": java_conformance_inputs(build_root),
        }
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, tarfile.TarError) as error:
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
            or record.get("fingerprint") != key or record.get("repository") != repo.name
            or not isinstance(record.get("evidence"), dict)
            or not isinstance(record["evidence"].get("resolvedManifests"), dict)
            or not isinstance(record["evidence"].get("dependencies"), list)):
        return None
    try:
        datetime.fromisoformat(record['passedAt'].replace('Z', '+00:00'))
    except (KeyError, AttributeError, TypeError, ValueError):
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


def save(cedar_home, key, inputs, package, evidence=None):
    """Record a passing result. Recording it again for the same inputs replaces it whole."""
    directory = results_root(cedar_home)
    directory.mkdir(parents=True, exist_ok=True)
    record = {"schemaVersion": SCHEMA, "fingerprint": key, "repository": inputs["repository"],
              "package": package, "passedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "inputs": inputs, "evidence": evidence}
    descriptor, temporary = tempfile.mkstemp(prefix="result-", suffix=".json", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, directory / f"{key}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

"""Resolve a frontend's CEDAR dependencies from the siblings just built, not from Nexus.

`cedarcli build java` never consults a pin. It builds the repositories in dependency order,
installing each into `~/.m2`, so every consumer compiles against the sibling that came out of the
working tree a moment earlier. `-SNAPSHOT` matters only to consumers outside that walk. The local
property comes from the reactor, and this is the reactor for the frontends.

npm has no moving coordinate to borrow. Every consumer names an exact immutable version in its
package.json and again in its lock, `npm ci` reads the lock rather than a dist-tag, and a range over
these prereleases resolves back to release-time builds. So a reactor build rewrites the dependency
to a local path instead, and that path is the point of this module.

It cannot be the sibling's checkout. A published package is not its source tree: the model library
builds a `dist/` whose package.json is `package-dist.json`, the two Web Components stage under
`dist-npm/`, and the design tokens publish their root. Each repository declares which it is.

Each successful producer is packed without lifecycle scripts and stored as an immutable,
content-addressed npm tarball. Consumers install the tarball, never a shared directory link.
A build snapshots the available packages once and then uses its own producers' outputs;
concurrent builds cannot change that selection. Producers scheduled in this build must
succeed before their consumers may resolve them. Other dependencies retain their pins
when no local artifact is available.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from org.metadatacenter.npm_package import NpmPackageError, pack_and_inspect

# Beside the checkouts rather than inside one, because it belongs to no repository. The name is
# hidden so it does not read as a sibling to anything that scans $CEDAR_HOME for repositories.
STORE = ".reactor"


DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies")

SKIPPED_DIRECTORIES = {"node_modules", "dist", "dist-npm", "dist-bundle", ".angular", ".git"}


def store_root(cedar_home) -> Path:
    return Path(cedar_home) / STORE


def unscoped(name: str) -> str:
    return name.rsplit("/", 1)[-1] if name else name


def install_commands(commands):
    """The same commands, with every npm ci turned into npm install.

    npm ci installs the lock and refuses a package.json that disagrees with it, which a rewritten
    dependency always does. The copy's lock is discarded with the copy, so nothing is lost.

    Done on tokens rather than by pattern, because the commands here are not all `npm ci`:
    `npm --prefix visual ci` carries a flag and its value in between, and `npm run ci` would be a
    script of that name rather than the subcommand.
    """
    rewritten = []
    for command in commands:
        tokens = command.split()
        if tokens[:1] == ["npm"] and "ci" in tokens and "run" not in tokens:
            tokens[tokens.index("ci")] = "install"
            command = " ".join(tokens)
        rewritten.append(command)
    return rewritten


class ReactorError(RuntimeError):
    """A reactor artifact could not be produced or safely consumed."""


_session = ContextVar("cedar_frontend_reactor", default=None)


def _available(cedar_home):
    refs = store_root(cedar_home) / "refs"
    available = {}
    try:
        for ref in sorted(refs.glob("*.json")):
            digest = json.loads(ref.read_text())["sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"invalid artifact digest in {ref}")
            target = store_root(cedar_home) / "artifacts" / f"{digest}.tgz"
            if not target.is_file():
                raise ValueError(f"missing artifact {target}")
            available[ref.stem] = target.resolve()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReactorError(f"Cannot read reactor store: {error}") from error
    return available


@contextlib.contextmanager
def session(cedar_home, producers=()):
    """Freeze external selections and require this walk's producers to finish first."""
    available = _available(cedar_home)
    pending = set(producers)
    for name in pending:
        available.pop(name, None)
    token = _session.set((Path(cedar_home).resolve(), available, pending))
    try:
        yield
    finally:
        _session.reset(token)


def _state(cedar_home):
    state = _session.get()
    if state is not None and state[0] == Path(cedar_home).resolve():
        return state[1], state[2]
    return _available(cedar_home), set()


def session_for_plan(cedar_home, plan):
    """Only isolated frontend tasks participate; other plans never read the store."""
    repos = []

    def visit(task):
        if getattr(task, "parameters", {}).get("isolated_frontend_build") is True:
            repos.append(task.repo)
        for child in task.tasks:
            visit(child)

    visit(plan)
    if not repos:
        return contextlib.nullcontext()
    return session(cedar_home, {repo.name for repo in repos if repo.published_package_path})


def publish(repo, build_root, cedar_home, environment=None) -> str | None:
    """Pack verified output and atomically advertise its immutable artifact.

    Declaring a package makes storage part of build success. Never accept absent output,
    keep a partially written artifact, or report a failed store operation as success.
    """
    if not repo.published_package_path:
        return None
    source = (Path(build_root) / repo.published_package_path).resolve()
    try:
        package = json.loads((source / "package.json").read_text(encoding="utf-8"))
        name = unscoped(package.get("name") or "")
        if name != repo.name or not isinstance(package.get("version"), str) or not package["version"]:
            raise ValueError(f"expected named, versioned package for {repo.name}")
        store = store_root(cedar_home).resolve()
        artifacts = store / "artifacts"
        refs = store / "refs"
        artifacts.mkdir(parents=True, exist_ok=True)
        refs.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pack-", dir=store) as temporary:
            scratch = Path(temporary)
            packed, inspection = pack_and_inspect(
                source, scratch, environment=environment, cache=scratch / "cache")
            digest = inspection.sha256
            destination = artifacts / f"{digest}.tgz"
            # An exclusive link makes a complete artifact visible in one operation. The
            # same digest can be published concurrently without replacing either reader's file.
            try:
                os.link(packed, destination)
            except FileExistsError:
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise ValueError(f"corrupt existing artifact {destination}")
            ref = scratch / "reference.json"
            ref.write_text(json.dumps({"sha256": digest}) + "\n")
            os.replace(ref, refs / f"{name}.json")
        state = _session.get()
        if state is not None and state[0] == Path(cedar_home).resolve():
            state[1][name] = destination
            state[2].discard(name)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, NpmPackageError) as error:
        raise ReactorError(f"Cannot store {repo.name}: {error}") from error
    return name


def resolve(build_root, cedar_home) -> list[str]:
    """Point every dependency the store holds at the store, in this copy only.

    Every manifest in the copy is rewritten, because a repository can carry several: CEE has one
    under visual/, and each multi-repository has one per sub-project.
    """
    available, pending = _state(cedar_home)
    if not available and not pending:
        return []
    notes = []
    for manifest in sorted(Path(build_root).rglob("package.json")):
        if SKIPPED_DIRECTORIES & set(manifest.parts):
            continue
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(package, dict):
            continue
        rewritten = False
        for section in DEPENDENCY_SECTIONS:
            declared = package.get(section)
            if not isinstance(declared, dict):
                continue
            for key, requested in list(declared.items()):
                name = key
                if isinstance(requested, str) and requested.startswith("npm:"):
                    name = requested[len("npm:"):].rsplit("@", 1)[0]
                # A repository never resolves itself from the store.
                if unscoped(name) == unscoped(package.get("name") or ""):
                    continue
                if unscoped(name) in pending:
                    raise ReactorError(f"{manifest}: producer {unscoped(name)} has not succeeded in this build")
                target = available.get(unscoped(name))
                if target is None:
                    continue
                declared[key] = f"file:{target}"
                rewritten = True
                notes.append(f"{manifest.parent.name}/{key} -> {target.name}")
        if rewritten:
            manifest.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    return notes

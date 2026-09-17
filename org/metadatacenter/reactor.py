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

Nor can it be the sibling's build output, because a frontend builds in a copy that is thrown away.
That is what `~/.m2` is for in Maven, and this keeps the same shape: after a repository builds, its
published package is copied into a store beside the checkouts, and a consumer's dependency is
rewritten to point there.

A dependency the store has not seen is left alone. So an empty store builds exactly what the pins
say, which is what every build did before this existed, and `build frontends` fills the store as it
walks: the producers come first, so by the time a consumer builds, the siblings it needs are there.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

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


def publish(repo, build_root, cedar_home) -> str | None:
    """Copy what this repository publishes into the store, for the consumers still to build.

    Returns what it stored, for the build report, or None when this repository publishes nothing
    a sibling consumes.
    """
    if not repo.published_package_path:
        return None
    source = (Path(build_root) / repo.published_package_path).resolve()
    manifest = source / "package.json"
    if not manifest.is_file():
        return None
    try:
        name = unscoped(json.loads(manifest.read_text(encoding="utf-8")).get("name") or "")
    except (OSError, ValueError):
        return None
    if not name:
        return None
    destination = store_root(cedar_home) / name
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=True,
                        ignore=lambda _d, names: {n for n in names if n == "node_modules"})
    except OSError as error:
        return f"{name} could not be stored: {error}"
    return name


def resolve(build_root, cedar_home) -> list[str]:
    """Point every dependency the store holds at the store, in this copy only.

    Every manifest in the copy is rewritten, because a repository can carry several: CEE has one
    under visual/, and each multi-repository has one per sub-project.
    """
    store = store_root(cedar_home)
    if not store.is_dir():
        return []
    available = {path.name: path for path in store.iterdir() if (path / "package.json").is_file()}
    if not available:
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
                target = available.get(unscoped(name))
                # A repository never resolves itself from the store.
                if target is None or unscoped(name) == unscoped(package.get("name") or ""):
                    continue
                declared[key] = f"file:{target}"
                rewritten = True
                notes.append(f"{manifest.parent.name}/{key} -> {target.name}")
        if rewritten:
            manifest.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    return notes

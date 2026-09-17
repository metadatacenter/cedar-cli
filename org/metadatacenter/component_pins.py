"""Advance a component's published dev snapshot, and the consumer pins that follow it.

The Java estate gets this for nothing. `cedar-parent` names `cedar.version` once, every child pom
names dependencies without a version, and the coordinate is a `-SNAPSHOT`, which Maven re-resolves
from Nexus on every build. A merge to develop publishes a snapshot and every downstream build picks
it up with no source edit anywhere.

npm has neither half. Each consumer declares an exact immutable version in its own package.json and
again in its lock, and there is no moving coordinate to stand in for a snapshot: `npm ci` reads the
lock rather than a dist-tag, and a semver range over these prereleases snaps back to release-time
builds. The lock is what makes a build reproducible, so the pin has to be exact, and advancing it is
therefore a source change across several repositories rather than a property of the build.

That is why this is a command and not something `cedarcli build frontends` does. A build that
rewrote package.json and package-lock.json would mutate tracked files mid-build, which the isolated
frontend workspace already refuses, and it would make a train's output depend on when it ran rather
than on the commits it captured.

So the work is explicit, and it is the same three steps a person otherwise does by hand: stamp the
component's next development version from its develop head, publish the package it stages, and
repoint every declared consumer's manifest, lock and served bundles onto it. Nothing is committed.
The diffs are source changes and belong to whoever reviews them.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Column, Table

from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util

console = Console()

CONFIG = Path("cedar-development") / "ops" / "frontend-train.json"

# A component's version is either a plain base version or that base with a development suffix
# naming the day and the commit it was built from.
VERSION = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?:-dev\.\d{8}\.[0-9a-f]{7,40})?$")

# How a consumer reaches a scoped package through npm's alias form.
ALIAS_PREFIX = "npm:"

# Long enough to stay unambiguous in every CEDAR repository, and what the components already carry.
SHA_LENGTH = 8


class ComponentPinError(Exception):
    """The work could not be planned or carried out, as distinct from having nothing to do."""


@dataclass(frozen=True)
class ConsumerPlan:
    repository: str
    dependency: str
    manifest: str
    lock: str
    current: str | None
    target: str
    restage: tuple[str, ...]

    @property
    def moves(self) -> bool:
        return self.current != self.target


@dataclass(frozen=True)
class ComponentPlan:
    identifier: str
    repository: str
    package: str
    staged: str
    dist: tuple[str, ...]
    published: str
    head: str
    target: str
    consumers: tuple[ConsumerPlan, ...]

    @property
    def publishes(self) -> bool:
        """Whether develop holds a commit the published version does not name."""
        return self.published != self.target

    @property
    def moves(self) -> bool:
        return self.publishes or any(consumer.moves for consumer in self.consumers)


def advance_component_pins(apply=False, only=None):
    """Report what would move, and under --apply move it.

    Reporting is the default because publishing is irreversible: an npm version, once taken, cannot
    be republished, so the plan is worth reading before it is carried out.
    """
    try:
        plans = plan(Util.cedar_home, only=only)
    except ComponentPinError as error:
        console.print(f"[red]{error}[/red]")
        return 1

    _report(plans, apply)
    if not any(item.moves for item in plans):
        return 0
    if not apply:
        console.print("\nNothing has been changed. Re-run with --apply to publish and repoint.")
        return 0
    try:
        _apply(Util.cedar_home, plans)
    except ComponentPinError as error:
        console.print(f"[red]{error}[/red]")
        return 1
    return 0


# Planning


def declared(cedar_home, only=None):
    """The components the frontend configuration declares, validated enough to act on."""
    if not cedar_home:
        raise ComponentPinError("CEDAR_HOME does not name a workspace")
    path = Path(cedar_home) / CONFIG
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ComponentPinError(f"cannot read {CONFIG}: {error}") from error
    items = config.get("components")
    if not isinstance(items, list) or not items:
        raise ComponentPinError(f"{CONFIG} declares no components")
    selected = []
    for item in items:
        if not isinstance(item, dict):
            raise ComponentPinError(f"{CONFIG} has a component that is not an object")
        for field in ("id", "repository", "publishedName", "stagedPackage", "distCommand"):
            if not item.get(field):
                raise ComponentPinError(f"{CONFIG} component {item.get('id')!r} has no {field}")
        if only and item["id"] != only:
            continue
        selected.append(item)
    if only and not selected:
        names = ", ".join(sorted(item["id"] for item in items if isinstance(item, dict)))
        raise ComponentPinError(f"no component is declared as {only!r}; declared: {names}")
    return selected


def plan(cedar_home, only=None):
    """What each declared component and each of its consumers would move to."""
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    plans = []
    for item in declared(cedar_home, only=only):
        directory = Path(cedar_home) / item["repository"]
        published = _manifest_version(directory / item.get("sourceManifest", "package.json"))
        head = _head(directory)
        target = next_version(published, head, today)
        consumers = tuple(
            _consumer_plan(cedar_home, consumer, item["publishedName"], target)
            for consumer in item.get("consumers", [])
        )
        plans.append(ComponentPlan(
            identifier=item["id"],
            repository=item["repository"],
            package=item["publishedName"],
            staged=item["stagedPackage"],
            dist=tuple(item["distCommand"]),
            published=published,
            head=head,
            target=target,
            consumers=consumers,
        ))
    return plans


def next_version(published: str, head: str, today: str) -> str:
    """The development version for this head, keeping the base the component already carries.

    A published version that already names this head is returned unchanged, so a component whose
    develop has not moved is not republished under a new day's stamp.
    """
    match = VERSION.match(published or "")
    if not match:
        raise ComponentPinError(f"{published!r} is not a version this can advance")
    if published.endswith(f".{head}"):
        return published
    return f"{match.group('base')}-dev.{today}.{head}"


def _consumer_plan(cedar_home, consumer, package, target):
    for field in ("repository", "dependency", "manifest"):
        if not consumer.get(field):
            raise ComponentPinError(f"a consumer of {package} has no {field}")
    manifest = Path(cedar_home) / consumer["repository"] / consumer["manifest"]
    declared_value = _dependency_value(manifest, consumer["dependency"])
    return ConsumerPlan(
        repository=consumer["repository"],
        dependency=consumer["dependency"],
        manifest=consumer["manifest"],
        lock=consumer.get("lock", "package-lock.json"),
        current=_version_of(declared_value, package),
        target=target,
        restage=tuple(consumer.get("restageCommand", [])),
    )


def _version_of(declared_value, package):
    """The version a declared dependency asks for, whether written plainly or through an alias."""
    if declared_value is None:
        return None
    value = declared_value.strip()
    if value.startswith(ALIAS_PREFIX):
        prefix = f"{ALIAS_PREFIX}{package}@"
        return value[len(prefix):] if value.startswith(prefix) else value
    return value


def _dependency_value(manifest, dependency):
    package = _read_json(manifest)
    if package is None:
        raise ComponentPinError(f"cannot read {manifest}")
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        declared_section = package.get(section)
        if isinstance(declared_section, dict) and dependency in declared_section:
            return declared_section[dependency]
    return None


def _manifest_version(path):
    package = _read_json(path)
    if package is None or not isinstance(package.get("version"), str):
        raise ComponentPinError(f"{path} declares no version")
    return package["version"]


def _head(directory):
    completed = _git(directory, ["rev-parse", f"--short={SHA_LENGTH}", "--verify", "develop"])
    if completed is None:
        raise ComponentPinError(f"cannot read the develop head of {directory.name}")
    return completed


def _git(directory, arguments):
    completed = subprocess.run(["git", "-C", str(directory)] + arguments,
                               capture_output=True, text=True, check=False,
                               env=invocation_environment())
    return completed.stdout.strip() if completed.returncode == 0 else None


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# Carrying it out


def _apply(cedar_home, plans, run=None):
    """Publish each component that has moved, then repoint every consumer that follows it.

    Every repository this would write to is required to be clean in its tracked files first, so the
    diffs left behind are this command's own and a review sees nothing else.
    """
    run = run or _run
    _require_clean(cedar_home, plans)
    for item in plans:
        if not item.moves:
            continue
        directory = Path(cedar_home) / item.repository
        if item.publishes:
            console.print(f"[bold]{item.repository}[/bold] → {item.target}")
            _stamp(directory / "package.json", item.target)
            _stamp_lock(directory / "package-lock.json", item.target)
            run(directory, list(item.dist))
            run(directory, ["npm", "publish", f"./{item.staged}", "--tag=dev"])
        for consumer in item.consumers:
            if not consumer.moves:
                continue
            console.print(f"  {consumer.repository}: {consumer.dependency} → {item.target}")
            consumer_directory = Path(cedar_home) / consumer.repository
            _repoint(consumer_directory / consumer.manifest, consumer.dependency,
                     item.package, item.target)
            run(consumer_directory, ["npm", "install"])
            if consumer.restage:
                run(consumer_directory, list(consumer.restage))
    console.print("\nNothing is committed. Review each repository's diff, then commit and push it.")


def _require_clean(cedar_home, plans):
    touched = set()
    for item in plans:
        if not item.moves:
            continue
        if item.publishes:
            touched.add(item.repository)
        touched.update(consumer.repository for consumer in item.consumers if consumer.moves)
    dirty = []
    for repository in sorted(touched):
        status = _git(Path(cedar_home) / repository,
                      ["status", "--porcelain=v1", "--untracked-files=no"])
        if status is None:
            raise ComponentPinError(f"cannot read the state of {repository}")
        if status:
            dirty.append(repository)
    if dirty:
        raise ComponentPinError(
            "these repositories hold uncommitted tracked changes, and this command writes to "
            "them: " + ", ".join(dirty))


def _stamp(path, version):
    package = _read_json(path)
    if package is None:
        raise ComponentPinError(f"cannot read {path}")
    package["version"] = version
    _write_json(path, package)


def _stamp_lock(path, version):
    """A lockfile carries the package's own version twice, in the root and under the empty key."""
    lock = _read_json(path)
    if lock is None:
        return
    lock["version"] = version
    packages = lock.get("packages")
    if isinstance(packages, dict) and isinstance(packages.get(""), dict):
        packages[""]["version"] = version
    _write_json(path, lock)


def _repoint(path, dependency, package, version):
    """Write the new version into the consumer's manifest, keeping the form it already uses."""
    manifest = _read_json(path)
    if manifest is None:
        raise ComponentPinError(f"cannot read {path}")
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        declared_section = manifest.get(section)
        if isinstance(declared_section, dict) and dependency in declared_section:
            current = str(declared_section[dependency]).strip()
            declared_section[dependency] = (
                f"{ALIAS_PREFIX}{package}@{version}" if current.startswith(ALIAS_PREFIX)
                else version
            )
            _write_json(path, manifest)
            return
    raise ComponentPinError(f"{path} does not declare {dependency}")


def _write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _run(directory, command):
    completed = subprocess.run(command, cwd=str(directory), check=False,
                               env=invocation_environment())
    if completed.returncode:
        raise ComponentPinError(
            f"{' '.join(command)} failed in {directory.name} with {completed.returncode}")


# Reporting


def _report(plans, apply):
    table = Table(
        "Component",
        "Surface",
        Column(header="From"),
        Column(header="To"),
    )
    for item in plans:
        table.add_row(
            item.repository,
            f"published package ({item.head})",
            item.published,
            item.target if item.publishes else "[green]current[/green]",
        )
        for consumer in item.consumers:
            table.add_row(
                "",
                f"  {consumer.repository} pin",
                consumer.current or "[yellow]not declared[/yellow]",
                consumer.target if consumer.moves else "[green]current[/green]",
            )
    moving = [item for item in plans if item.moves]
    table.caption = (f"{len(moving)}/{len(plans)} components would move"
                     if not apply else f"applying {len(moving)}/{len(plans)} components")
    console.print(table)

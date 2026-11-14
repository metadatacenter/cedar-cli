"""Publish registry packages and update consumers' checked-in dependencies.

Local snapshot builds use `cedarcli build frontends` (or `cedarcli test frontends` for checks).
The reactor builds working-tree sources and installs their tarballs into isolated consumers,
without waiting for commits, CI publication or checked-in pin updates. This module is only
for recording registry dependencies that standalone `npm ci` and consumer CI will install.
Its publication restrictions are not prerequisites for a local snapshot build.

For that registry workflow, derive the
component's development version from its pushed develop head, build and publish the package that
version names, and repoint every declared consumer's manifest, lock and served bundles onto it.
By default nothing is committed, and the diffs belong to whoever reviews them. `--commit` commits
each repository's pin moves with a message naming each one, `--push` pushes them, and `--settle`
repeats the passes a release's aftermath needs until nothing moves.

The version is written into the component's manifest only while its package builds, and the
manifest is restored afterwards. A committed stamp would itself be a new head, and the next run
would publish that head again under a version naming it, then ask for that stamp to be committed in
turn. The version is a property of the commit instead, `<base>-dev.<commit date>.<head>`, and
whether it is published is the registry's answer, not the manifest's.

Some components are published by something else, and are followed rather than published here. The
model library's CI and CEE's each publish a development package from every push to develop, and
nothing else ever repointed their consumers to one: after a release CEE's consumers stayed on the
public CEE until someone moved all of them by hand.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Column, Table

from org.metadatacenter import npm_install
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

# CEE's CI names the commit it built in seven characters.
CI_SHA_LENGTH = 7

# The name every CEE consumer knows the editor by, public or development.
CEE_DEPENDENCY = "cedar-embeddable-editor"

# A release's aftermath settles in two passes. More than this means something keeps moving.
MAX_PASSES = 4

# How long --settle waits for a component's CI to publish its pushed head, and how often it asks.
WAIT_MINUTES = 30
WAIT_POLL_SECONDS = 60

# The trailer that marks a commit this command made, so a later pass may amend it before it is
# pushed rather than stacking a second commit on it.
TRAILER = "Pins-moved-by: cedarcli publish components"

# How a commit message names the packages it moves.
PIN_NAMES = {
    "cedar-model-typescript-library": "model library",
    "cedar-embeddable-editor": "CEE",
    "cedar-embeddable-designer": "CED",
    "cedar-embeddable-term-picker": "CETP",
    "cedar-design-tokens": "shared token",
}


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
        return self.target is not None and self.current != self.target


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
    published_by: str | None = None
    # Followed from the package its CI published for its develop head, rather than from a
    # reference consumer.
    follows_head: bool = False
    # Why a component has no version to move its consumers to.
    blocked: str | None = None
    # Whether the registry already holds the version this component's head publishes.
    held: bool = False

    @property
    def publishes(self) -> bool:
        """Whether this command would publish, which a component it does not own never does.

        A followed component's target is the package something else published: the one its CI
        published for its develop head, or the version a reference consumer already carries.
        """
        if self.published_by or self.blocked:
            return False
        return not self.held

    @property
    def moves(self) -> bool:
        if self.blocked:
            return False
        return self.publishes or any(consumer.moves for consumer in self.consumers)


def advance_component_pins(apply=False, only=None, commit=False, push=False, settle=False,
                           wait_minutes=WAIT_MINUTES, sleep=time.sleep, clock=time.monotonic):
    """Report what would move, and under --apply move it.

    Reporting is the default because publishing is irreversible: an npm version, once taken, cannot
    be republished, so the plan is worth reading before it is carried out.

    A component whose own pins move in a pass has a new head that no package holds yet, so its
    consumers can follow it only in a later pass. After a release that takes two passes, with
    CEE's CI publishing in between. `settle` runs them: it pushes each component the pass wrote,
    waits for CI to publish what it follows, and passes again until nothing moves. Consumers'
    commits stay local until the last pass, which amends its pin moves into them, so each
    consumer takes one commit.
    """
    commit = commit or push
    if (commit or settle) and not apply:
        console.print("[red]--commit, --push and --settle act on what --apply writes; add --apply.[/red]")
        return 1
    if settle and not push:
        console.print("[red]--settle waits for CI to publish pushed heads, so it needs --push.[/red]")
        return 1
    committed, pushed = set(), set()
    applied = False
    try:
        for passed in range(1, MAX_PASSES + 1):
            plans = plan(Util.cedar_home, only=only)
            _report(plans, apply)
            if not any(item.moves for item in plans):
                break
            if not apply:
                console.print("\nNothing has been changed. Re-run with --apply to publish and repoint.")
                return 0
            moved, deferred = _apply(Util.cedar_home, plans)
            applied = True
            if commit:
                committed |= _commit_moves(Util.cedar_home, moved)
            components = {item.repository for item in plans} & set(moved)
            if not settle or not (deferred or components):
                break
            # A component this pass wrote has a head no package holds. Push it, so its CI publishes
            # or the next pass publishes it, then pass again.
            pushed |= _push(Util.cedar_home, sorted(components & committed))
            _wait_for_ci(Util.cedar_home, components, only, wait_minutes, sleep, clock)
        else:
            raise ComponentPinError(f"pins still moved after {MAX_PASSES} passes; read the plan")
        if push:
            pushed |= _push(Util.cedar_home, sorted(committed - pushed))
    except ComponentPinError as error:
        console.print(f"[red]{error}[/red]")
        return 1
    if applied and not commit:
        console.print("\nNothing is committed. Review each repository's diff, then commit and push it.")
    elif push and committed:
        console.print(f"\nCommitted and pushed: {', '.join(sorted(committed))}.")
    elif committed:
        console.print(f"\nCommitted, not pushed: {', '.join(sorted(committed))}.")
    return 0


def _wait_for_ci(cedar_home, components, only, wait_minutes, sleep, clock):
    """Wait until CI has published a package for each followed component's pushed head."""
    deadline = clock() + wait_minutes * 60
    announced = None
    while True:
        waiting = [item for item in plan(cedar_home, only=only)
                   if item.repository in components and item.follows_head and item.blocked]
        if not waiting:
            return
        if clock() >= deadline:
            raise ComponentPinError(
                "CI has not published after "
                f"{wait_minutes} minutes: " + "; ".join(item.blocked for item in waiting))
        reasons = tuple(item.blocked for item in waiting)
        if reasons != announced:
            console.print("Waiting for CI: " + "; ".join(reasons))
            announced = reasons
        sleep(WAIT_POLL_SECONDS)


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
    items = [_with_cee_model_pins(item, config) for item in items] + _ci_published(config)
    selected = []
    for item in items:
        if not isinstance(item, dict):
            raise ComponentPinError(f"{CONFIG} has a component that is not an object")
        item = dict(item, registry=item.get("registry") or config.get("registry"))
        required = ("id", "repository", "publishedName")
        if item.get("followsHead"):
            required += ("registry",)
        elif item.get("publishedBy"):
            required += ("reference",)
        else:
            required += ("stagedPackage", "distCommand", "registry")
        for field in required:
            if not item.get(field):
                raise ComponentPinError(f"{CONFIG} component {item.get('id')!r} has no {field}")
        if only and item["id"] != only:
            continue
        selected.append(item)
    if only and not selected:
        names = ", ".join(sorted(item["id"] for item in items if isinstance(item, dict)))
        raise ComponentPinError(f"no component is declared as {only!r}; declared: {names}")
    return selected


def _with_cee_model_pins(item, config):
    """A component that repoints CEE's model pin repoints all of them.

    CEE pins the model library twice, in its root manifest and in its visual suite's, and the
    configuration records the second under `cee.additionalModelConsumers` rather than as a
    consumer of its own, since the suite is not a surface anything verifies.
    """
    cee = config.get("cee")
    if not isinstance(item, dict) or not isinstance(cee, dict):
        return item
    dependency = cee.get("modelDependency")
    consumers = item.get("consumers") or []
    if not any(isinstance(consumer, dict) and consumer.get("repository") == cee.get("repository")
               and consumer.get("dependency") == dependency for consumer in consumers):
        return item
    extra = [dict(consumer, repository=cee["repository"], dependency=dependency)
             for consumer in cee.get("additionalModelConsumers", []) if isinstance(consumer, dict)]
    return dict(item, consumers=list(consumers) + extra)


def _ci_published(config):
    """CEE, whose development packages its own CI publishes from every push to develop.

    Its consumers are the inventory a release pins the public CEE into, each frontend's
    `ceeConsumer` and the additional ones, so the two lists cannot drift apart.
    """
    cee = config.get("cee")
    if not isinstance(cee, dict) or not cee.get("repository"):
        return []
    consumers = [dict(item["ceeConsumer"], repository=item["repository"])
                 for item in config.get("frontends", [])
                 if isinstance(item, dict) and isinstance(item.get("ceeConsumer"), dict)]
    consumers += [dict(item) for item in config.get("additionalCeeConsumers", [])
                  if isinstance(item, dict)]
    return [{
        "id": "cee",
        "repository": cee["repository"],
        "publishedName": cee.get("publishedName"),
        "sourceManifest": cee.get("sourceManifest", "package.json"),
        "publishedBy": "repository's CI",
        "followsHead": True,
        "registry": config.get("registry"),
        "consumers": [dict(consumer, dependency=consumer.get("dependency", CEE_DEPENDENCY))
                      for consumer in consumers],
    }]


def plan(cedar_home, only=None):
    """What each declared component and each of its consumers would move to."""
    plans = []
    for item in declared(cedar_home, only=only):
        directory = Path(cedar_home) / item["repository"]
        published = _manifest_version(directory / item.get("sourceManifest", "package.json"))
        head = _head(directory)
        published_by = item.get("publishedBy")
        blocked = None
        held = False
        if item.get("followsHead"):
            target, blocked = _ci_head_version(cedar_home, item)
        elif published_by:
            target = _reference_version(cedar_home, item)
        else:
            target, blocked = _publishable_version(cedar_home, item, published, head)
            held = bool(target) and _registry_holds(item["registry"], item["publishedName"], target)
        consumers = tuple(
            _consumer_plan(cedar_home, consumer, item["publishedName"], target)
            for consumer in item.get("consumers", [])
        )

        plans.append(ComponentPlan(
            identifier=item["id"],
            repository=item["repository"],
            package=item["publishedName"],
            staged=item.get("stagedPackage", ""),
            dist=tuple(item.get("distCommand", ())),
            published=published,
            head=head,
            target=target,
            consumers=consumers,
            published_by=published_by,
            follows_head=bool(item.get("followsHead")),
            blocked=blocked,
            held=held,
        ))
    return plans


def development_version(carried: str, head: str, date: str) -> str:
    """The development version a head publishes: the base the component carries, then the date
    of the head's commit and the head itself.

    The date is the commit's rather than the day of publication, as CEE's CI names its packages,
    so a head published once is recognised as published on any later day.
    """
    match = VERSION.match(carried or "")
    if not match:
        raise ComponentPinError(f"{carried!r} is not a version this can advance")
    return f"{match.group('base')}-dev.{date}.{head}"


def _publishable_version(cedar_home, item, carried, head):
    """The version this component's develop head publishes under, or why it cannot publish.

    A package names the commit it was built from, so only a pushed head may be published: an
    unpushed one names a commit that no other checkout can reach.
    """
    directory = Path(cedar_home) / item["repository"]
    if _git(directory, ["rev-parse", "--verify", "origin/develop"]) != \
            _git(directory, ["rev-parse", "--verify", "develop"]):
        return None, "develop is not the pushed origin/develop, so a package could not name it"
    # The package is built from the checkout, so a checkout elsewhere would publish other source
    # under develop's name.
    if _git(directory, ["rev-parse", "--verify", "HEAD"]) != \
            _git(directory, ["rev-parse", "--verify", "develop"]):
        return None, "the checkout is not at develop's head, so the package would not hold what it names"
    date = _git(directory, ["show", "-s", "--format=%cd", "--date=format:%Y%m%d", "develop"],
                environment={"TZ": "UTC"})
    return development_version(carried, head, date), None


def _reference_version(cedar_home, item):
    """The version a followed component's reference consumer already carries.

    A followed component's own manifest is not the answer. The model library's package.json
    names the commit of its last stamp, which is not always the newest snapshot the train
    published, so the consumer the train keeps current is the reliable witness.
    """
    reference = item["reference"]
    for field in ("repository", "dependency"):
        if not reference.get(field):
            raise ComponentPinError(f"{item['id']}'s reference has no {field}")
    manifest = (Path(cedar_home) / reference["repository"]
                / reference.get("manifest", "package.json"))
    version = _version_of(_dependency_value(manifest, reference["dependency"]),
                          item["publishedName"])
    if not version:
        raise ComponentPinError(
            f"{reference['repository']} does not declare {reference['dependency']}, "
            f"so {item['id']} has no version to follow")
    return version


def _ci_head_version(cedar_home, item):
    """The development version the component's CI published from its pushed develop head, or why none.

    The CI names its package `<base>-dev.<date>.<sha7>` after the commit it built, reading the base
    from the committed manifest and the date with the git invocation below, so this derives it the
    same way. Consumers follow the newest package, as a Maven build takes the newest snapshot, so the
    head is origin/develop as fetched now. Local commits not yet pushed and uncommitted edits are in
    no package, and are what a development build uses from the checkout, so they hold nothing back. A
    release preparation head, and a version the registry does not hold yet, each leave the consumers
    where they are, and the plan says which.
    """
    directory = Path(cedar_home) / item["repository"]
    _git(directory, ["fetch", "--quiet", "origin", "develop"])
    head = _git(directory, ["rev-parse", "--verify", "origin/develop"])
    if head is None:
        raise ComponentPinError(f"cannot read the pushed develop head of {directory.name}")
    manifest = item.get("sourceManifest", "package.json")
    committed = _git(directory, ["show", f"origin/develop:{manifest}"])
    try:
        version = json.loads(committed or "")["version"]
    except (ValueError, KeyError, TypeError) as error:
        raise ComponentPinError(f"{directory.name}'s committed {manifest} declares no version") from error
    if "-dev." not in version:
        return None, (f"develop carries the release version {version}, "
                      "from which its CI publishes no development package")
    date = _git(directory, ["show", "-s", "--format=%cd", "--date=format:%Y%m%d", "origin/develop"],
                environment={"TZ": "UTC"})
    sha = _git(directory, ["rev-parse", f"--short={CI_SHA_LENGTH}", "origin/develop"])
    target = f"{version.split('-')[0]}-dev.{date}.{sha}"
    if not _registry_holds(item["registry"], item["publishedName"], target):
        return None, f"{target} is not in the registry yet; its CI run on {sha} publishes it"
    return target, None


def _registry_holds(registry, package, version):
    """Whether the registry serves this exact version's tarball."""
    name = package.rsplit("/", 1)[-1]
    url = f"{registry.rstrip('/')}/{package}/-/{name}-{version}.tgz"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise ComponentPinError(f"{url} answered HTTP {error.code}") from error
    except (urllib.error.URLError, OSError) as error:
        raise ComponentPinError(f"cannot reach {registry}: {error}") from error


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


def _git(directory, arguments, environment=None):
    completed = subprocess.run(["git", "-C", str(directory)] + arguments,
                               capture_output=True, text=True, check=False,
                               env={**invocation_environment(), **(environment or {})})
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
    # A pin update is not a deployment. npm install (including lifecycle scripts)
    # and explicit restaging can overwrite a running reactor with older registry pins.
    preserve_runtime = (
        invocation_environment().get("CEDAR_PROFILE") != "server"
        and (Path(cedar_home) / ".reactor/runtime.json").is_file()
    )
    written = set()
    moved = {}
    deferred = []
    for item in plans:
        if not item.moves:
            continue
        if (item.publishes or item.follows_head) and item.repository in written:
            # Its head does not hold the pins this run just gave it, so no package names them.
            console.print(f"[yellow]{item.repository}[/yellow] moved in this run, so no package "
                          "holds what it now declares: commit and push it, then run this again.")
            deferred.append(item)
            continue
        directory = Path(cedar_home) / item.repository
        if item.publishes:
            console.print(f"[bold]{item.repository}[/bold] → {item.target}")
            _publish(directory, item, run)
        for consumer in item.consumers:
            if not consumer.moves:
                continue
            console.print(f"  {consumer.repository}: {consumer.dependency} → {item.target}")
            written.add(consumer.repository)
            record = moved.setdefault(consumer.repository, {"files": set(), "pins": {}})
            record["files"].update(path for path in (consumer.manifest, consumer.lock) if path)
            record["pins"][item.package.rsplit("/", 1)[-1]] = item.target
            consumer_directory = Path(cedar_home) / consumer.repository
            _repoint(consumer_directory / consumer.manifest, consumer.dependency,
                     item.package, item.target)
            install = ["npm", "install"]
            if preserve_runtime:
                install.extend(["--package-lock-only", "--ignore-scripts"])
            run((consumer_directory / consumer.manifest).parent, install)
            if consumer.restage and not preserve_runtime:
                run(consumer_directory, list(consumer.restage))
    if preserve_runtime:
        console.print("Active development reactor preserved; pins updated without replacing installed or served bundles.")
    return moved, deferred


def pin_commit_message(pins):
    """A message naming each pin a commit moves, in the words the history already uses."""
    names = []
    for package in sorted(pins):
        name = PIN_NAMES.get(package, package)
        if name not in names:
            names.append(name)
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    noun = "pin" if len(pins) == 1 else "pins"
    lines = [f"Update the {listed} development {noun}", "",
             "Moves each pin to the development package built from that component's",
             "current develop head.", ""]
    lines += [f"- {package} {pins[package]}" for package in sorted(pins)]
    return "\n".join(lines + ["", TRAILER, ""])


def _git_checked(directory, arguments):
    completed = subprocess.run(["git", "-C", str(directory)] + arguments, capture_output=True,
                               text=True, check=False, env=invocation_environment())
    if completed.returncode:
        raise ComponentPinError(
            f"git {' '.join(arguments[:2])} failed in {Path(directory).name}: "
            f"{(completed.stderr or completed.stdout).strip()}")
    return completed.stdout.strip()


def _own_unpushed_commit(directory):
    """The pins an unpushed commit this command made already moved, or None for any other HEAD."""
    body = _git(directory, ["log", "-1", "--format=%B"]) or ""
    if TRAILER not in body or _git(directory, ["branch", "-r", "--contains", "HEAD"]):
        return None
    return dict(line[2:].rsplit(" ", 1) for line in body.splitlines()
                if line.startswith("- ") and " " in line[2:])


def _commit_moves(cedar_home, moved):
    """Commit each repository's pin moves, amending this command's own unpushed commit."""
    committed = set()
    for repository, record in sorted(moved.items()):
        directory = Path(cedar_home) / repository
        files = sorted(path for path in record["files"]
                       if (directory / path).is_file()
                       and _git(directory, ["status", "--porcelain=v1", "--", path]))
        if not files:
            continue
        earlier = _own_unpushed_commit(directory)
        pins = {**(earlier or {}), **record["pins"]}
        amend = ["--amend"] if earlier is not None else []
        _git_checked(directory, ["commit", "--quiet", *amend, "-m", pin_commit_message(pins),
                                 "--", *files])
        committed.add(repository)
    return committed


def _push(cedar_home, repositories):
    """Push develop in each repository, which must be on develop."""
    pushed = set()
    for repository in repositories:
        directory = Path(cedar_home) / repository
        branch = _git(directory, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch != "develop":
            raise ComponentPinError(f"{repository} is on {branch or 'no branch'}, not develop; nothing pushed")
        _git_checked(directory, ["push", "--quiet", "origin", "develop"])
        pushed.add(repository)
    return pushed


def _publish(directory, item, run):
    """Build and publish the head's package, with its version stamped only while it builds.

    The manifest and lock are restored whatever happens. Consumer pins may have moved without
    replacing local reactor installs, so the build installs from the declared lock rather than from
    whatever node_modules holds.
    """
    manifest, lock = directory / "package.json", directory / "package-lock.json"
    originals = {path: path.read_bytes() for path in (manifest, lock) if path.is_file()}
    try:
        _stamp(manifest, item.target)
        _stamp_lock(lock, item.target)
        run(directory, ["npm", "ci"])
        run(directory, list(item.dist))
        run(directory, ["npm", "publish", _publish_target(item.staged), "--tag=dev"])
    finally:
        for path, payload in originals.items():
            path.write_bytes(payload)


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
            # Each line is a two-letter status, then the path; the first may have lost its leading space.
            paths = [line[2:].strip() for line in status.splitlines() if line.strip()]
            dirty.append(f"{repository} ({', '.join(paths)})")
    if dirty:
        raise ComponentPinError(
            "these repositories hold uncommitted tracked changes, and this command writes to "
            "them: " + "; ".join(dirty) + ". Commit those changes first, then run this again; "
            "its pin moves can be committed on top or amended into that commit.")


def _publish_target(staged):
    """The directory npm publishes, for a component that stages one and for one that does not.

    A component whose published package is its checkout root, such as the design tokens, declares
    "." and is published in place.
    """
    return "." if staged.strip(" /") in ("", ".") else f"./{staged}"


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
    """Write the new version into the consumer's manifest.

    A consumer that names the published package itself gets a plain version, which is what npm
    canonicalizes a same-name alias to. One that knows it by another name needs the alias, and that
    is decided by the names rather than by the pin it replaces: CEE's consumers carry the public
    `cedar-embeddable-editor` plainly after a release, and its development package is scoped.
    """
    manifest = _read_json(path)
    if manifest is None:
        raise ComponentPinError(f"cannot read {path}")
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        declared_section = manifest.get(section)
        if isinstance(declared_section, dict) and dependency in declared_section:
            declared_section[dependency] = (
                version if dependency == package else f"{ALIAS_PREFIX}{package}@{version}")
            _write_json(path, manifest)
            return
    raise ComponentPinError(f"{path} does not declare {dependency}")


def _write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _run(directory, command):
    """Run a command, and an npm install under the rule that it runs again if npm dropped an
    optional dependency: a publish built from such an install ships without that dependency."""
    if npm_install.is_install(" ".join(command)):
        npm_install.install(lambda attempt: _run_once(directory, attempt), command,
                            lambda message: console.print(message, markup=False), ComponentPinError)
    else:
        _run_once(directory, command)


def _run_once(directory, command):
    completed = subprocess.run(command, cwd=str(directory), check=False,
                               env=invocation_environment())
    if completed.returncode:
        raise ComponentPinError(
            f"{' '.join(command)} failed in {directory.name} with {completed.returncode}")


# Reporting


def _report(plans, apply):
    console.print("Registry dependency update. For a local snapshot build, run cedarcli build frontends; "
                  "it uses working-tree components without waiting for registry publication.")
    table = Table(
        "Component",
        "Surface",
        Column(header="From"),
        Column(header="To"),
    )
    for item in plans:
        surface = (f"published by the {item.published_by}" if item.published_by
                   else f"published package ({item.head})")
        table.add_row(
            item.repository,
            surface,
            item.published,
            "[dim]not published here[/dim]" if item.published_by
            else "[yellow]held back[/yellow]" if item.blocked
            else (item.target if item.publishes else "[green]published[/green]"),
        )
        if item.blocked:
            table.add_row("", f"  [yellow]consumers stay: {item.blocked}[/yellow]", "", "")
        for consumer in item.consumers:
            table.add_row(
                "",
                f"  {consumer.repository} pin",
                consumer.current or "[yellow]not declared[/yellow]",
                consumer.target if consumer.moves
                else "[yellow]stays[/yellow]" if item.blocked else "[green]current[/green]",
            )
    moving = [item for item in plans if item.moves]
    table.caption = (f"{len(moving)}/{len(plans)} components would move"
                     if not apply else f"applying {len(moving)}/{len(plans)} components")
    console.print(table)

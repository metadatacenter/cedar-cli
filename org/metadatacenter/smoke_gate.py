"""The whole-stack smoke run as evidence a train dispatch and a release plan are gated on.

The estate's rule is that suites verify logic and a redeploy plus a smoke run verifies reality.
Until this module nothing enforced the second half: the smoke tiers under `cedar-development/ops/e2e`
ran when a developer remembered, and a contract regression that every backend-free suite passes
could reach staging through a train. `cedarcli test e2e` runs every tier against the native stack
and records what they ran against. The browser tier drives the combined AngularJS application; the
split tier drives the Angular Workspace, the Designer host and the account pages that replace it. The train dispatch preflight and the
release plan refuse a source that no passing run covers.

A run is evidence about commits, not about a moment. The record therefore names the `develop` head
of every train repository at the time of the run, and the gate compares those heads with the source
being trained or released. A run is kept under a digest of the heads it tested rather than as one
latest file, because a train is released hours or days after it is dispatched and `develop` moves
on in between: a rerun after a documentation commit must not erase the evidence the release still
needs.

The record must also answer for what the stack runs. The controller's own `current` column
establishes that no process predates its jar, which is a narrower question than it looks: a jar can
itself predate the commit the record names, and every row reads `current` while it does. A run
therefore refuses to start while any service is stale or unhealthy, while any deployed jar was
written before its own repository's head, its parent's or a library's reached this machine, while a
server jar embeds a build of a library other than that library's latest, while the frontend
reactor's deployed build was made from commits other than the heads the record would name, while a
frontend serves a package, a copy or a tree whose stamp does not name them, and while Keycloak runs
an event listener other than develop's. The evidence would otherwise certify code the stack is not
running.
"""

from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.local_stack_lock import local_stack

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Mapping

from rich.console import Console

from org.metadatacenter.worker.ServerWorker import ServerWorker

console = Console()

SCHEMA_VERSION = 1
TIERS = ("rest", "browser", "split", "search")
REMEDY = "run cedarcli test e2e against the stack built from this source"

E2E = Path("cedar-development") / "ops" / "e2e"
REPORTS = E2E / "reports" / "smoke-gate"
BUILD_TRAIN = Path("cedar-development") / "ops" / "build-train.json"
CONTROLLER = Path("cedar-development") / "ops" / "cedar-services.sh"

HEALTHY = {"healthy"}
SUMMARY_LIMIT = 5
# split-tier.mjs exits with this status when another split run holds its lock, and writes no report.
SPLIT_LOCK_REFUSED = 75
# The bounds of the split tier's cap on any one unit's deadline, in seconds, as split-tier.mjs takes it.
SPLIT_TIMEOUT_CAP_RANGE = (60, 7200)
# The REST tier's wall-clock budget. Its runner stops itself after 1,200 seconds and tears down, and
# each request it makes times out; this is the backstop for a runner that does neither.
REST_BUDGET_SECONDS = 1800


class SmokeGateError(ValueError):
    """The gate could not be answered from what is recorded."""


def _wait_for_rest_preflight(rest, path):
    """Do not let browser fixtures enter REST's initial clean-stack inventory.

    An older or failed runner without this signal safely runs to completion first;
    its existing subprocess deadline still bounds that wait. Each attempt owns a
    fresh signal path, so an earlier run can never release this barrier.
    """
    while not rest.done():
        try:
            signal = json.loads(path.read_text(encoding="utf-8"))
            if (signal.get("schemaVersion") == 1 and signal.get("preflightComplete") is True
                    and isinstance(signal.get("runId"), str) and signal["runId"]):
                return
        except (OSError, ValueError, AttributeError):
            pass
        time.sleep(0.02)


def _home(cedar_home) -> Path:
    if not cedar_home:
        raise SmokeGateError("CEDAR_HOME is not set")
    return Path(cedar_home)


def reports_dir(cedar_home) -> Path:
    return _home(cedar_home) / REPORTS


def train_repositories(cedar_home) -> list[str]:
    """The repositories a train captures, which are the ones a run must speak for."""
    path = _home(cedar_home) / BUILD_TRAIN
    try:
        configuration = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeGateError(f"cannot read build-train configuration: {error}") from error
    repositories = configuration.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise SmokeGateError(f"{path} names no repositories")
    return list(repositories)


def sources_digest(sources: Mapping[str, str]) -> str:
    """One name for one set of heads, so a rerun against other heads lands beside it, not on it."""
    canonical = json.dumps(sorted(sources.items()), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _capture(runner, args, cwd=None) -> tuple[int, str, str]:
    try:
        result = runner(
            list(args), cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=False,
            env=invocation_environment(),
        )
    except OSError as error:
        return 127, "", str(error)
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def absent_repositories(cedar_home, repositories: Iterable[str]) -> list[str]:
    """The train repositories not checked out on this machine, which no run here can have tested."""
    home = _home(cedar_home)
    return [repository for repository in repositories if not (home / repository / ".git").exists()]


def develop_heads(
    cedar_home, repositories: Iterable[str], runner=subprocess.run,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Every checked-out train repository's local `develop`, and which of them hold uncommitted work.

    What the stack runs is the working tree, so the record can name `develop` only when the working
    tree is `develop`. A checkout on another branch, or detached at another commit, is a problem
    rather than a head: a run against it would certify `develop` for code that is not on it.

    An untracked file makes a repository dirty. The train cannot ship it, but the build compiled it
    and the stack ran it, so a record that called the repository clean would say the run tested a
    commit that does not hold it. A repository absent from this machine is left out here and named
    by ``absent_repositories``.
    """
    home = _home(cedar_home)
    heads: dict[str, str] = {}
    dirty: list[str] = []
    problems: list[str] = []
    for repository in repositories:
        root = home / repository
        if not (root / ".git").exists():
            continue
        code, head, detail = _capture(runner, ["git", "rev-parse", "refs/heads/develop"], cwd=root)
        if code != 0 or not head:
            problems.append(f"{repository} has no local develop branch"
                            + (f": {detail.splitlines()[-1]}" if detail else ""))
            continue
        code, checked_out, _ = _capture(runner, ["git", "rev-parse", "HEAD"], cwd=root)
        if code == 0 and checked_out and checked_out != head:
            _, branch, _ = _capture(runner, ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=root)
            where = f"{branch} checked out" if branch else f"a detached HEAD at {checked_out[:8]}"
            problems.append(f"{repository} has {where}, not develop, so the stack runs code that is "
                            "not on develop")
            continue
        heads[repository] = head
        code, status, _ = _capture(runner, ["git", "status", "--porcelain"], cwd=root)
        if code == 0 and status:
            dirty.append(repository)
    return heads, dirty, problems


CLI = "cedar-cli"


def running_cli_findings(cedar_home, heads: Mapping[str, str], module: Path | None = None) -> list[str]:
    """Why the CLI running this gate is not the cedar-cli checkout whose head the record names.

    cedar-cli and cedar-development are never built or deployed: the gate and the tiers run
    straight from their checkouts, so their heads and working trees are what ran. That holds only
    when this module is the checkout's own, not a worktree or another copy of the CLI.
    """
    if CLI not in heads:
        return []
    running = (module or Path(__file__)).resolve()
    checkout = (_home(cedar_home) / CLI).resolve()
    if checkout in running.parents:
        return []
    return [f"the gate runs from {running.parent}, not the {CLI} checkout at {checkout} whose head "
            "the record would name; run cedarcli from that checkout"]


def native_processes(cedar_home, runner=subprocess.run) -> dict[str, str] | None:
    """The process serving each managed service, from the controller, or None when it cannot say."""
    code, output, _detail = _capture(runner, [str(_home(cedar_home) / CONTROLLER), "status-tsv"])
    if code != 0:
        return None
    try:
        return {row["service"]: row["pid"] for row in ServerWorker.parse_native_status(output.splitlines())}
    except ValueError:
        return None


def changes_during_run(cedar_home, repositories, runner, *, heads, dirty, processes, frontend_build) -> list[str]:
    """What moved while the tiers ran, which a record would otherwise attribute to the heads it names.

    The stack is judged once before the tiers start, and a run takes long enough for another session
    to commit, to edit a tree an `ng serve` frontend compiles from, or to restart a service, none of
    which waits for the local-stack lock. A head that moved, a clean tree that gained changes, a
    service served by another process, a service that went stale or behind its source, and another
    frontend build each mean some tiers ran other code than the record names. A repository that was
    already dirty is recorded as dirty, which the gate refuses whatever happened to it.
    """
    changes = []
    after, after_dirty, problems = develop_heads(cedar_home, repositories, runner)
    changes.extend(f"after the run: {problem}" for problem in problems)
    for name in sorted(set(heads) | set(after)):
        if heads.get(name) != after.get(name):
            changes.append(f"{name} moved from {str(heads.get(name))[:8]} to {str(after.get(name))[:8]} "
                           "while the tiers ran")
    changes.extend(f"{name} gained uncommitted changes while the tiers ran"
                   for name in sorted(set(after_dirty) - set(dirty)))
    now = native_processes(cedar_home, runner)
    if now is None:
        changes.append("the native stack's status cannot be read after the run")
    else:
        for service in sorted(set(processes) | set(now)):
            if processes.get(service) != now.get(service):
                changes.append(f"{service} was served by another process after the run "
                               f"(pid {processes.get(service) or 'none'}, then {now.get(service) or 'none'})")
    warming = tuple(f" is {state}" for state in WARMING_HEALTH)
    changes.extend(f"after the run: {finding}" for finding in stack_findings(cedar_home, runner)
                   if not finding.endswith(warming))
    _problems, _built_dirty, build = frontend_sources(cedar_home, heads)
    if build != frontend_build:
        changes.append("the frontends were redeployed with another reactor build while the tiers ran")
    return changes


def stack_findings(cedar_home, runner=subprocess.run) -> list[str]:
    """Why the running stack cannot stand for its source, or nothing when it can.

    The controller's status is the only view that knows both whether a service answers and whether
    the process serving it predates its jar. A stale service passes every request and still makes a
    green run meaningless, and a frontend serving an Embeddable Editor other than the one its lock
    names is stale in exactly the same sense.
    """
    controller = _home(cedar_home) / CONTROLLER
    code, output, detail = _capture(runner, [str(controller), "status-tsv"])
    if code != 0:
        return ["cannot read native stack status"
                + (f": {detail.splitlines()[-1]}" if detail else "")]
    try:
        rows = ServerWorker.parse_native_status(output.splitlines())
    except ValueError as error:
        return [str(error)]
    findings = []
    for row in rows:
        service = row["service"]
        if row["pid"].startswith("!"):
            findings.append(f"{service} is served by a process the controller does not manage")
        elif row["health"] == "docker":
            # A container answers for an image, and nothing here says which commit built it.
            findings.append(f"{service} is served by a container, so the run cannot say which commit "
                            "it tested")
        elif row["health"] not in HEALTHY:
            findings.append(f"{service} is {row['health']}")
        if row["binary"] == "STALE":
            findings.append(f"{service} is stale: its process predates the code it should serve")
        elif row["binary"] == "MISSING":
            findings.append(f"{service} runs with no jar built for its version, so its process "
                            "predates a version change")
    findings.extend(source_currency_findings(
        cedar_home,
        [row["service"] for row in rows if row["binary"] in {"current", "STALE"}],
        runner=runner,
    ))
    findings.extend(keycloak_listener_findings(cedar_home, runner))
    return findings


KEYCLOAK_LISTENER = "cedar-keycloak-event-listener"
# The port the controller's infrastructure table gives Keycloak.
KEYCLOAK_PORT = 8080


def keycloak_started(runner=subprocess.run) -> float | None:
    """When the process listening on Keycloak's port started, or None when that cannot be read."""
    code, pids, _detail = _capture(runner, ["lsof", "-nP", f"-iTCP:{KEYCLOAK_PORT}", "-sTCP:LISTEN", "-t"])
    pid = pids.split()[0] if code == 0 and pids.split() else ""
    if not pid.isdigit():
        return None
    code, started, _detail = _capture(runner, ["env", "LC_ALL=C", "ps", "-o", "lstart=", "-p", pid])
    try:
        return time.mktime(time.strptime(started.strip(), "%a %b %d %H:%M:%S %Y")) if code == 0 else None
    except ValueError:
        return None


def keycloak_listener_findings(cedar_home, runner=subprocess.run) -> list[str]:
    """Why Keycloak cannot be shown to run the event listener develop holds, or nothing when it can.

    The listener is built by Maven but deployed by copying its jar into Keycloak's providers
    directory, so no controller row and no jar date speaks for it. The jar carries cedar-parent's
    stamp, and Keycloak loads its providers when it starts, so the deployed jar must name the
    develop head and the process must have started after the jar was installed.
    """
    home = _home(cedar_home)
    if not (home / KEYCLOAK_LISTENER / ".git").exists():
        return []
    code, head, _detail = _capture(runner, ["git", "rev-parse", "refs/heads/develop"], cwd=home / KEYCLOAK_LISTENER)
    if code != 0 or not head:
        return []  # develop_heads names a repository with no develop branch.
    keycloak = Path(invocation_environment().get("CEDAR_KEYCLOAK_HOME") or home / "keycloak")
    jar = keycloak / "providers" / f"{KEYCLOAK_LISTENER}.jar"
    if not jar.is_file():
        return [f"Keycloak's providers directory holds no {jar.name}; run cedarcli dev copy-keycloak-listener "
                "and restart Keycloak"]
    remedy = "; run cedarcli build java, cedarcli dev copy-keycloak-listener and restart Keycloak"
    findings = [finding + remedy for finding in stamp_findings(
        "keycloak's event listener", jar, KEYCLOAK_LISTENER,
        lambda repository: head if repository == KEYCLOAK_LISTENER else None)]
    started = keycloak_started(runner)
    if started is not None and started < jar.stat().st_mtime:
        findings.append(f"keycloak started before its {jar.name} was installed, so it runs an earlier listener; "
                        "restart Keycloak")
    return findings


# The Java repositories whose classes the server jars embed, and the parent whose managed versions
# every server builds against. A commit to any of them reaches the stack only through a rebuild of
# the servers, which their own repositories' heads do not show.
JAVA_LIBRARIES = ("cedar-artifact-library", "cedar-config-library", "cedar-core-library",
                  "cedar-microservice-libraries", "cedar-model-library", "cedar-model-validation-library")
JAVA_PARENT = "cedar-parent"


def head_arrival(cedar_home, repository: str, runner=subprocess.run) -> tuple[float, str] | None:
    """When the local develop branch took its current commit, and that commit, or None.

    A commit's own date says when it was made, which can be long before it reached this machine: a
    commit made elsewhere before a build and pulled after it would pass a comparison with that date.
    The reflog records when the branch moved here, so the later of the two is the moment a build
    must follow. A branch without a reflog falls back to the commit's date.
    """
    root = _home(cedar_home) / repository
    if not (root / ".git").exists():
        return None
    code, head, _detail = _capture(
        runner, ["git", "log", "-1", "--format=%ct %H", "refs/heads/develop"], cwd=root)
    if code != 0 or not head.strip():
        return None
    committed, _, revision = head.strip().partition(" ")
    try:
        arrived = float(int(committed))
    except ValueError:
        return None
    code, moved, _detail = _capture(
        runner, ["git", "reflog", "show", "-1", "--date=unix", "--format=%gd", "refs/heads/develop"], cwd=root)
    stamp = moved.strip().rpartition("@{")[2].rstrip("}") if code == 0 else ""
    if stamp.isdigit():
        arrived = max(arrived, float(stamp))
    return arrived, revision


def _library_jars(root: Path, version: str | None) -> list[Path]:
    """The jars a library's local build wrote, without sources, javadoc, tests or shading inputs."""
    jars = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in {".git", "node_modules", "src"}]
        if os.path.basename(directory) != "target":
            continue
        subdirectories[:] = []
        for name in files:
            if (name.endswith(".jar") and not name.startswith("original-")
                    and not name.endswith(("-sources.jar", "-javadoc.jar", "-tests.jar"))
                    and (not version or name.endswith(f"-{version}.jar"))):
                jars.append(Path(directory) / name)
    return sorted(jars)


# Where cedar-parent's build stamp puts each module's commit: META-INF/cedar/<artifactId>.git.properties.
STAMPS = "META-INF/cedar/"
STAMP_SUFFIX = ".git.properties"


def jar_stamps(jar: Path) -> dict[str, dict[str, str]]:
    """The build stamps a jar carries, by module, each with its commit, dirtiness and origin.

    cedar-parent stamps every module at a path of its own, so a shaded server jar holds the stamp of
    each CEDAR module it embeds. Raises OSError or zipfile.BadZipFile for a jar that cannot be read.
    """
    stamps: dict[str, dict[str, str]] = {}
    with zipfile.ZipFile(jar) as archive:
        for entry in archive.infolist():
            name = entry.filename
            if not (name.startswith(STAMPS) and name.endswith(STAMP_SUFFIX)):
                continue
            properties = {}
            for line in archive.read(entry).decode("utf-8", "replace").splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "!")):
                    continue
                key, _, value = line.partition("=")
                # java.util.Properties escapes ':' and '=' in values, as in https\://github.com.
                properties[key.strip()] = re.sub(r"\\(.)", r"\1", value.strip())
            stamps[name[len(STAMPS):-len(STAMP_SUFFIX)]] = properties
    return stamps


def stamp_source(properties: Mapping[str, str]) -> tuple[str | None, str | None]:
    """The repository and commit a stamp names, from its origin's last path segment and its commit."""
    origin = properties.get("git.remote.origin.url", "").rstrip("/")
    repository = re.split(r"[/:]", origin)[-1].removesuffix(".git") if origin else None
    commit = properties.get("git.commit.id") or None
    return repository or None, commit


def stamp_findings(name: str, jar: Path, own: str, head_of) -> list[str]:
    """Why a jar's stamps do not show it was built from the develop heads, or nothing when they do.

    ``own`` is the repository the jar must carry a stamp of, and ``head_of`` answers the develop head
    of a repository checked out here, or None. Every stamp of a repository checked out here must name
    its head.
    """
    try:
        stamps = jar_stamps(jar)
    except (OSError, zipfile.BadZipFile) as error:
        return [f"{name} was built into a jar that cannot be read: {error}"]
    findings, sources = [], set()
    for module, properties in sorted(stamps.items()):
        repository, commit = stamp_source(properties)
        if repository is None or commit is None:
            findings.append(f"{name} was built with a stamp of {module} that names no repository or commit")
            continue
        sources.add(repository)
        head = head_of(repository)
        if head is not None and commit != head:
            findings.append(f"{name} was built from {repository} {commit[:8]}, not develop {head[:8]}")
    if own not in sources:
        findings.append(f"{name} was built without a stamp of {own}, so nothing says which commit it holds; "
                        "rebuild it with the current cedar-parent")
    return list(dict.fromkeys(findings))


def stamped_dirty(cedar_home, services) -> list[str]:
    """The repositories whose stamps in the deployed server jars say the build saw uncommitted changes."""
    dirty = set()
    for service in services:
        jar = application_jar(cedar_home, service)
        if jar is None:
            continue
        try:
            stamps = jar_stamps(jar)
        except (OSError, zipfile.BadZipFile):
            continue
        for properties in stamps.values():
            repository, _commit = stamp_source(properties)
            if repository and properties.get("git.dirty") == "true":
                dirty.add(repository)
    return sorted(dirty)


def library_builds(cedar_home, arrival) -> tuple[dict[str, dict[str, int]], list[str]]:
    """The classes of each Java library's latest local build, and why any cannot stand for its head.

    ``arrival`` answers ``head_arrival`` for a repository. A library whose build predates its head
    cannot hold it, and one never built here leaves the servers embedding whatever snapshot the
    build resolved, which no record can name.
    """
    home = _home(cedar_home)
    version = invocation_environment().get("CEDAR_VERSION")
    builds: dict[str, dict[str, int]] = {}
    findings: list[str] = []
    for library in JAVA_LIBRARIES:
        head = arrival(library)
        if head is None:
            continue
        jars = _library_jars(home / library, version)
        if not jars:
            findings.append(f"{library} was built nowhere on this machine, so nothing shows the servers "
                            f"embed develop {head[1][:8]}")
            continue
        behind = [finding for jar in jars
                  for finding in stamp_findings(library, jar, library, lambda repository: head[1] if repository == library else None)]
        if behind:
            findings.append(behind[0])
            continue
        classes: dict[str, int] = {}
        try:
            for jar in jars:
                with zipfile.ZipFile(jar) as archive:
                    classes.update({entry.filename: entry.CRC for entry in archive.infolist()
                                    if entry.filename.endswith(".class")})
        except (OSError, zipfile.BadZipFile) as error:
            findings.append(f"{library} was built into a jar that cannot be read: {error}")
            continue
        builds[library] = classes
    return builds, findings


def embedded_library_findings(service: str, jar: Path, libraries: Mapping[str, Mapping[str, int]]) -> list[str]:
    """Which libraries a server jar embeds in a build other than their latest local one.

    A server jar holds its libraries' classes byte for byte, so a class whose checksum differs from
    the library's own build shows the server was built against another build of it, typically an
    older snapshot. A library none of whose classes the jar holds is one the server does not use.
    """
    try:
        with zipfile.ZipFile(jar) as archive:
            served = {entry.filename: entry.CRC for entry in archive.infolist()}
    except (OSError, zipfile.BadZipFile) as error:
        return [f"{service} was built into a jar that cannot be read: {error}"]
    findings = []
    for library, classes in libraries.items():
        shared = [name for name in classes if name in served]
        differing = [name for name in shared if served[name] != classes[name]]
        if differing:
            findings.append(f"{service} was built before its libraries' latest build: it embeds a build of "
                            f"{library} in which {len(differing)} of {len(shared)} classes differ, such as "
                            f"{differing[0]}")
    return findings


def application_jar(cedar_home, service: str) -> Path | None:
    """The jar the controller would run for a microservice, or None when it has not been built.

    The controller runs the jar for ``CEDAR_VERSION`` and no other. Taking the newest jar of any
    version instead compared a jar the stack does not run, which after a version change can be the
    only current one in the directory.
    """
    target = (_home(cedar_home) / f"cedar-{service}-server"
              / f"cedar-{service}-server-application" / "target")
    version = invocation_environment().get("CEDAR_VERSION")
    if version:
        jar = target / f"cedar-{service}-server-application-{version}.jar"
        return jar if jar.is_file() else None
    jars = [
        path for path in target.glob(f"cedar-{service}-server-application-*.jar")
        if not path.name.startswith("original-")
    ]
    return max(jars, key=lambda path: path.stat().st_mtime) if jars else None


def source_currency_findings(cedar_home, services, runner=subprocess.run) -> list[str]:
    """Which deployed jars were written before the source the record will say they covered.

    The controller's `current` column compares a process with its jar, which answers whether a
    redeploy happened, not whether the jar holds the commit the record names. A jar written
    before its repository's develop head reached this machine cannot contain that head, and a
    run recorded against it certifies code the stack is not running. The same holds for the
    parent whose versions the servers build against and for every library whose classes they
    embed. A library is held to its head through its own build, and each server jar must embed
    that build's classes unchanged.
    """
    arrivals: dict[str, tuple[float, str] | None] = {}

    def arrival(repository):
        if repository not in arrivals:
            arrivals[repository] = head_arrival(cedar_home, repository, runner)
        return arrivals[repository]

    jars = {}
    for service in services:
        if not (_home(cedar_home) / f"cedar-{service}-server" / ".git").exists():
            continue
        jar = application_jar(cedar_home, service)
        if jar is not None:
            jars[service] = jar
    if not jars:
        return []
    libraries, findings = library_builds(cedar_home, arrival)
    parent = arrival(JAVA_PARENT)
    for service, jar in jars.items():
        repository = f"cedar-{service}-server"
        built = jar.stat().st_mtime
        findings.extend(stamp_findings(service, jar, repository,
                                       lambda source: (arrival(source) or (None, None))[1]))
        if parent is not None and built < parent[0]:
            findings.append(f"{service} was built before {JAVA_PARENT} develop {parent[1][:8]}, which "
                            "sets the versions it builds against")
        if libraries:
            findings.extend(embedded_library_findings(service, jar, libraries))
    return findings


CEE = "cedar-embeddable-editor"


def expected_cee_version(cedar_home, heads: Mapping[str, str], runner=subprocess.run) -> tuple[str | None, str | None]:
    """The CEE version the browser tier must find on screen, and why it cannot be read, if it cannot.

    The editor shows the version its package declares. Read at the recorded CEE head, it is the one
    every host the browser tier drives must render, so the tier fails when any of them serves
    another editor. A record that names no CEE head expects none.
    """
    head = heads.get(CEE)
    if head is None:
        return None, None
    code, text, detail = _capture(runner, ["git", "show", f"{head}:package.json"], cwd=_home(cedar_home) / CEE)
    try:
        if code != 0:
            raise ValueError(detail.splitlines()[-1] if detail else f"git show exited {code}")
        version = json.loads(text)["version"]
        if not isinstance(version, str) or not version.strip():
            raise ValueError("the package declares no version")
    except (ValueError, KeyError, TypeError) as error:
        return None, f"cannot read the version {CEE} declares at {head[:8]}: {error}"
    return version.strip(), None


def frontend_sources(cedar_home, heads: Mapping[str, str]) -> tuple[list[str], list[str], str | None]:
    """Which recorded heads the served frontends were not built from, and which they were built dirty.

    The frontend reactor records the commit of every repository it built, and whether the tree held
    uncommitted changes, and restarts the frontends with that build. A head the build did not take
    is code no frontend serves. Uncommitted edits are valid inputs to a development build, so a
    dirty build is not a reason to stop; the repository is recorded as dirty instead, which the
    gate refuses for a train or a release. Returns the findings, the dirty repositories and the
    build's digest.
    """
    from org.metadatacenter.reactor import store_root
    root = store_root(_home(cedar_home))
    try:
        runtime = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
        build = runtime["build"]
        repositories = json.loads((root / "builds" / f"{build}.json").read_text(encoding="utf-8"))["repositories"]
        if not isinstance(repositories, dict):
            raise TypeError("the build names no repositories")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return ([f"no frontend reactor deployment records which commits the frontends serve ({error}); "
                 "run cedarcli build frontends"], [], None)
    findings, dirty = [], []
    for path, record in sorted(repositories.items()):
        repository = Path(path).parts[0]
        if repository not in heads:
            continue
        source = record.get("source") if isinstance(record, dict) else None
        revision = source.get("revision") if isinstance(source, dict) else None
        if not isinstance(revision, str) or revision != heads[repository]:
            findings.append(f"the frontends serve {path} built from {str(revision)[:8]}, not develop "
                            f"{heads[repository][:8]}; run cedarcli build frontends")
        elif source.get("dirty") is not False:
            dirty.append(repository)
    served, served_dirty = served_bundle_findings(cedar_home, heads, repositories, runtime.get("packages") or {})
    return findings + served, sorted(set(dirty) | set(served_dirty)), build


FRONTEND_TRAIN = Path("cedar-development") / "ops" / "frontend-train.json"
# A host that keeps its own copy of a component's bundle serves that copy, not the installed
# package, so each copy must equal the file of the package whose stamp is read.
SERVED_COPIES = {
    "cedar-embeddable-editor": ("app/third_party_components/cedar-embeddable-editor",
                                ("cedar-embeddable-editor.js", "cedar-embeddable-editor.host-fonts.js")),
}
# The Template Designer serves its components from app/components and names their bytes beside them.
COMPONENT_MANIFEST = Path("app") / "components" / "manifest.json"
# The hosts whose development start records what it built, as their server payloads do.
BUILD_INFO_HOSTS = ("cedar-workspace", "cedar-template-designer")
BUILD_INFO = Path("app") / "config" / "build-info.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def served_tree_sha256(root: Path) -> str:
    """The digest a host's build identity records as bundleSha256: its app/ tree, less the record.

    The same manifest write-native-frontend-build-info.mjs and the hosts' development starts hash:
    one line of file digest and repository-relative path per file, in byte order of the paths.
    A symbolic link is read as the file it names.
    """
    app = root / "app"
    excluded = root / BUILD_INFO
    entries = []
    for directory, subdirectories, files in os.walk(app):
        linked = [name for name in subdirectories if (Path(directory) / name).is_symlink()]
        subdirectories[:] = [name for name in subdirectories if name not in linked]
        for name in files + linked:
            path = Path(directory) / name
            if path != excluded:
                entries.append((path.relative_to(root).as_posix().encode("utf-8"), path))
    manifest = "".join(f"{_sha256(path)}  {relative.decode('utf-8')}\n" for relative, path in sorted(entries))
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def package_stamp_finding(host: str, name: str, installed: Path, source: Mapping) -> str | None:
    """Why a host's installed package does not show it was built from the source the reactor recorded.

    The reactor stamps every package it stores with the commit, dirty state and content digest of
    the source it built. A package reused for identical content keeps the stamp of the build that
    made it, so a matching content digest stands for the recorded commit.
    """
    from org.metadatacenter.reactor import BUILD_STAMP
    try:
        package = json.loads((installed / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return f"{host} has no readable {name} package installed ({error}); run cedarcli build frontends"
    stamp = package.get(BUILD_STAMP) if isinstance(package, dict) else None
    commit = stamp.get("sourceCommit") if isinstance(stamp, dict) else None
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return (f"{host} serves {name} from a package with no build stamp, so nothing says which commit "
                "it holds; run cedarcli build frontends")
    revision = source.get("revision")
    if commit == revision or (stamp.get("sourceSha256") is not None
                              and stamp.get("sourceSha256") == source.get("sourceSha256")):
        return None
    return (f"{host} serves {name} built from {commit[:8]}, not the reactor's {str(revision)[:8]}; "
            "run cedarcli build frontends")


def served_copy_findings(host: str, root: Path, installed: Mapping[str, Path]) -> list[str]:
    """Which component copies a host serves from its own tree differ from the package it installed."""
    findings = []
    for name, (directory, files) in SERVED_COPIES.items():
        if name not in installed:
            continue
        for file in files:
            served = root / directory / file
            if not served.exists():
                continue
            try:
                equal = served.read_bytes() == (installed[name] / file).read_bytes()
            except OSError:
                equal = False
            if not equal:
                findings.append(f"{host} serves a {file} that is not the one in its installed {name} package; "
                                "run cedarcli build frontends")
    return findings


def component_manifest_findings(host: str, root: Path, installed: Mapping[str, Path]) -> list[str]:
    """Why the components a host's manifest names are not the bytes it serves or the packages it installed."""
    path = root / COMPONENT_MANIFEST
    if not path.is_file():
        return []
    try:
        served = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(served, dict):
            raise ValueError("it is not an object")
    except (OSError, ValueError) as error:
        return [f"{host}'s served component manifest cannot be read: {error}"]
    findings = []
    for name, entry in sorted(served.items()):
        if not isinstance(entry, dict) or entry.get("source") != "package":
            findings.append(f"{host} serves {name} from a local override, which no build stamp identifies")
            continue
        if name not in installed:
            continue
        try:
            packaged = _sha256(installed[name] / str(entry.get("bundle")))
            delivery = entry.get("delivery")
            if isinstance(delivery, dict):
                delivered = _sha256(root / COMPONENT_MANIFEST.parent / str(delivery.get("file"))) == delivery.get("sha256")
            else:
                delivered = _sha256(root / COMPONENT_MANIFEST.parent / f"{name}.js") == entry.get("sha256")
        except OSError:
            packaged, delivered = None, False
        if packaged != entry.get("sha256") or not delivered:
            findings.append(f"{host} serves {name} bytes other than its installed package's; "
                            "run cedarcli build frontends")
    return findings


def build_info_findings(host: str, root: Path, head: str) -> tuple[list[str], bool]:
    """Why a host's recorded build identity does not stand for develop, and whether it was built dirty."""
    try:
        info = json.loads((root / BUILD_INFO).read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            raise ValueError("it is not an object")
    except (OSError, ValueError) as error:
        return [f"{host} serves no readable build identity at /{BUILD_INFO.relative_to('app').as_posix()} "
                f"({error}); restart it with cedarcli build frontends"], False
    commit, dirty = info.get("sourceCommit"), info.get("sourceDirty")
    if info.get("application") != host or not isinstance(dirty, bool):
        return [f"{host}'s build identity names {info.get('application')!r} and no dirty state; "
                "restart it with cedarcli build frontends"], False
    if commit != head:
        return [f"{host} serves a build of {str(commit)[:8]}, not develop {head[:8]}; "
                "restart it with cedarcli build frontends"], dirty
    try:
        current = served_tree_sha256(root)
    except OSError as error:
        return [f"{host}'s served tree cannot be read: {error}"], dirty
    if info.get("bundleSha256") != current:
        return [f"{host}'s build identity does not describe the tree it serves, which changed after the "
                "build; restart it with cedarcli build frontends"], dirty
    return [], dirty


def served_bundle_findings(cedar_home, heads, repositories, packages) -> tuple[list[str], list[str]]:
    """Why what each frontend serves is not what the reactor's build record says it built.

    The record names the commit of every repository the reactor built, but a host serves the
    packages installed in its checkout and the tree its start built there. Each installed package
    must carry the stamp of the source the record names, each copy a host serves of a component
    must be that package's bytes, and a host that records its own build identity must name its
    develop head and the tree it serves. Returns the findings and the hosts built dirty.
    """
    from org.metadatacenter.reactor import DEPENDENCY_SECTIONS, dependency_name
    home = _home(cedar_home)
    try:
        applications = {frontend["repository"] for frontend in
                        json.loads((home / FRONTEND_TRAIN).read_text(encoding="utf-8"))["frontends"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f"the frontend inventory cannot be read, so nothing shows what the frontends serve ({error})"], []
    findings, dirty = [], []
    for path in sorted(repositories):
        repository = Path(path).parts[0]
        if repository not in applications or repository not in heads:
            continue
        root = home / path
        try:
            manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            findings.append(f"{path} has no readable package.json ({error})")
            continue
        installed = {}
        for section in DEPENDENCY_SECTIONS:
            declared = manifest.get(section) if isinstance(manifest, dict) else None
            for key, requested in (declared.items() if isinstance(declared, dict) else ()):
                name = dependency_name(key, requested)
                if name in packages and isinstance(repositories.get(name), dict):
                    installed[name] = root / "node_modules" / key
        for name, directory in sorted(installed.items()):
            source = repositories[name].get("source")
            finding = package_stamp_finding(path, name, directory, source if isinstance(source, dict) else {})
            if finding:
                findings.append(finding)
        findings.extend(served_copy_findings(path, root, installed))
        findings.extend(component_manifest_findings(path, root, installed))
        if path in BUILD_INFO_HOSTS:
            problems, built_dirty = build_info_findings(path, root, heads[repository])
            findings.extend(problems)
            if built_dirty:
                dirty.append(repository)
    return findings, sorted(set(dirty))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _stamp(moment: dt.datetime) -> str:
    return moment.replace(microsecond=0).isoformat()


# A service in one of these states is on its way to healthy rather than broken, and refusing the gate
# for it turns a wait into a failed command an operator reruns by hand. A `starting` service has begun
# listening and has not finished booting, which a frontend compiling its bundle takes minutes to do.
# A `slow` one is serving and did not answer its health probe inside the bound, which a single slow
# dependency produces and the next poll usually clears.
WARMING_HEALTH = ("starting", "slow")
WARMUP_WAIT_SECONDS = 300
WARMUP_POLL_SECONDS = 15


def only_warming(problems: list[str]) -> bool:
    """Whether every finding is a service still coming up, rather than one that is wrong."""
    suffixes = tuple(f" is {state}" for state in WARMING_HEALTH)
    return bool(problems) and all(problem.endswith(suffixes) for problem in problems)


def wait_out_warmup(home, problems, runner=subprocess.run, sleeper=time.sleep):
    """Give a warming stack the time it needs, and return the findings that outlast it.

    The bound is a number of polls rather than a wall clock, so the wait is exactly as long as
    the polls it performs and a caller supplying its own sleeper controls the whole duration.
    """
    console.print(
        f"Waiting up to {WARMUP_WAIT_SECONDS // 60} minutes for the stack to finish warming: "
        + ", ".join(problems))
    for _attempt in range(max(1, WARMUP_WAIT_SECONDS // WARMUP_POLL_SECONDS)):
        sleeper(WARMUP_POLL_SECONDS)
        problems = stack_findings(home, runner)
        if not problems:
            console.print("The stack is healthy; running the smoke tiers.")
            return problems
        if not only_warming(problems):
            return problems
    return problems


def run_smoke(
    cedar_home=None,
    *,
    rest_workers: int | None = None,
    split_lanes: int | None = None,
    split_timeout_cap: int | None = None,
    runner=subprocess.run,
    clock: Callable[[], dt.datetime] = _now,
    environment: Mapping[str, str] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Run every smoke tier and record what they ran against. Zero only when all of them pass.

    The stack is judged before anything runs, and a refusal writes no record: an unhealthy or stale
    stack is a reason to stop, not evidence about the source. Every tier runs even when an earlier
    one fails, so one command yields one complete answer. The REST tier runs beside the others and
    writes its output to a file beside its report; the browser, split and search tiers run in turn.
    """
    if rest_workers is not None and (type(rest_workers) is not int or not 1 <= rest_workers <= 4):
        raise ValueError("REST workers must be an integer from 1 to 4")
    if split_lanes is not None and (type(split_lanes) is not int or not 1 <= split_lanes <= 6):
        raise ValueError("split lanes must be an integer from 1 to 6")
    if split_timeout_cap is not None and (
            type(split_timeout_cap) is not int
            or not SPLIT_TIMEOUT_CAP_RANGE[0] <= split_timeout_cap <= SPLIT_TIMEOUT_CAP_RANGE[1]):
        raise ValueError("the split tier's timeout cap must be an integer number of seconds from "
                         f"{SPLIT_TIMEOUT_CAP_RANGE[0]} to {SPLIT_TIMEOUT_CAP_RANGE[1]}")
    home = _home(cedar_home or invocation_environment().get("CEDAR_HOME"))
    with local_stack(home, console.print):
        return _run_tiers(home, rest_workers=rest_workers, split_lanes=split_lanes,
                          split_timeout_cap=split_timeout_cap, runner=runner, clock=clock,
                          environment=environment, sleeper=sleeper)


def _run_tiers(home, *, rest_workers, split_lanes, split_timeout_cap, runner, clock, environment,
               sleeper) -> int:
    """The smoke itself, run while this process holds the local stack."""
    environment = dict(environment if environment is not None else invocation_environment())
    e2e = home / E2E
    if not (e2e / "package.json").exists():
        console.print(f"[red]{e2e} is not the e2e checkout; nothing to run[/red]")
        return 1
    if shutil.which("npm", path=environment.get("PATH")) is None:
        console.print("[red]npm is not on PATH; the smoke tiers need Node[/red]")
        return 1

    problems = stack_findings(home, runner)
    if only_warming(problems):
        problems = wait_out_warmup(home, problems, runner=runner, sleeper=sleeper)
    if problems:
        console.print("[red]The native stack cannot stand for its source:[/red]")
        for problem in problems:
            console.print(f"  - {problem}", markup=False)
        console.print("Bring every service to healthy and current, then rerun cedarcli test e2e.")
        return 1

    repositories = train_repositories(home)
    heads, dirty, head_problems = develop_heads(home, repositories, runner)
    head_problems += running_cli_findings(home, heads)
    if head_problems:
        console.print("[red]Source heads cannot be recorded:[/red]")
        for problem in head_problems:
            console.print(f"  - {problem}", markup=False)
        return 1
    frontend_problems, built_dirty, frontend_build = frontend_sources(home, heads)
    if frontend_problems:
        console.print("[red]The frontends cannot stand for the source heads:[/red]")
        for problem in frontend_problems:
            console.print(f"  - {problem}", markup=False)
        return 1
    cee_version, cee_problem = expected_cee_version(home, heads, runner)
    if cee_problem:
        console.print(f"[red]{cee_problem}[/red]", markup=False)
        return 1
    if cee_version:
        # login-smoke-test.mjs compares every editor it renders with this version.
        environment["CEDAR_EXPECT_CEE_VERSION"] = cee_version
    processes = native_processes(home, runner)
    if processes is None:
        console.print("[red]cannot read native stack status[/red]")
        return 1
    tree_dirty = list(dirty)
    # A jar whose stamps say its build saw uncommitted changes holds code no commit does.
    dirty = sorted(set(dirty) | set(built_dirty) | (set(stamped_dirty(home, processes)) & set(heads)))
    if dirty:
        console.print(
            "[yellow]Uncommitted changes in: " + ", ".join(dirty)
            + ". The run is recorded, and the gate will refuse it for these repositories.[/yellow]")
    absent = absent_repositories(home, repositories)
    if absent:
        console.print(
            "[yellow]Not checked out here: " + ", ".join(absent)
            + ". The run cannot have tested them, and the gate will refuse a train or release that "
              "captures them.[/yellow]")

    digest = sources_digest(heads)
    reports = reports_dir(home)
    reports.mkdir(parents=True, exist_ok=True)
    # Each attempt owns its evidence, even when the tested source heads have not changed.
    rest_report = reports / f"rest-smoke-{digest}-{uuid.uuid4().hex}.json"
    # A run that crashes before writing its report must not show an earlier attempt's details.
    split_report = reports / f"split-smoke-{digest}-{uuid.uuid4().hex}.json"
    # A refused or killed run must never inherit an earlier search verdict at the same source heads.
    search_report = reports / f"search-smoke-{digest}-{uuid.uuid4().hex}.json"
    # Each browser script writes the steps it completed here, for comparison with the inventory.
    browser_reports = reports / f"browser-smoke-{digest}-{uuid.uuid4().hex}"
    rest_preflight = rest_report.with_suffix(".preflight.json")
    tier_environment = {
        "rest": {"CEDAR_REST_PREFLIGHT_REPORT": str(rest_preflight)},
        "browser": {"CEDAR_BROWSER_SMOKE_REPORTS": str(browser_reports)},
    }
    commands = {
        "rest": ["npm", "run", "smoke:rest", "--", f"--report={rest_report}"],
        "browser": ["npm", "run", "smoke"],
        "split": ["npm", "run", "smoke:workspace:modern:full", "--", f"--report={split_report}"],
        "search": ["npm", "run", "smoke:search", "--", f"--report={search_report}"],
    }
    if rest_workers is not None:
        commands["rest"].append(f"--workers={rest_workers}")
        commands["search"].append(f"--workers={rest_workers}")
    if split_lanes is not None:
        commands["split"].append(f"--lanes={split_lanes}")
    if split_timeout_cap is not None:
        commands["split"].append(f"--timeout-cap={split_timeout_cap}")

    # The REST tier's output goes to a file beside its report, since it runs beside the others.
    rest_log = rest_report.with_suffix(".log")

    def run(tier, **streams) -> dict:
        tier_started = clock()
        budget = {"timeout": REST_BUDGET_SECONDS} if tier == "rest" else {}
        timed_out = False
        try:
            result = runner(commands[tier], cwd=str(e2e), env={**environment, **tier_environment.get(tier, {})},
                            check=False, **streams, **budget)
            code = result.returncode
        except subprocess.TimeoutExpired:
            console.print(f"[red]the {tier} smoke did not finish within {REST_BUDGET_SECONDS} s[/red]")
            code = 124
            timed_out = True
        except OSError as error:
            console.print(f"[red]could not run the {tier} smoke: {error}[/red]")
            code = 127
        record = {
            "command": commands[tier],
            "exitCode": code,
            "verdict": "PASS" if code == 0 else "FAIL",
            "startedAt": _stamp(tier_started),
            "finishedAt": _stamp(clock()),
        }
        if tier == "rest":
            record["log"] = str(rest_log)
            record["budgetSeconds"] = REST_BUDGET_SECONDS
            record["timedOut"] = timed_out
            inventory = e2e / "rest" / "expected-checks.json"
            record.update(_rest_details(rest_report, inventory))
            dropped, unanswered = dropped_rest_checks(home, inventory, runner)
            record["droppedChecks"] = dropped
            if dropped or unanswered:
                record["evidenceValid"] = False
                record["reportError"] = unanswered or (
                    f"the REST inventory has lost {len(dropped)} check(s) the last passing run executed, "
                    f"and rest/retired-checks.json names no reason for them, such as "
                    f"{dropped[0].replace(chr(31), ' / ')}")
            if not record["evidenceValid"]:
                record["verdict"] = "FAIL"
        if tier == "browser":
            record["expectedCeeVersion"] = cee_version
            record.update(_browser_details(browser_reports, e2e / "browser-steps.json"))
        if tier == "split":
            record.update(_split_details(split_report, code))
            record.update(_split_evidence(split_report, e2e / "journey-scenarios.json", code))
        if tier in ("browser", "split") and not record["evidenceValid"]:
            record["verdict"] = "FAIL"
        if tier == "search":
            record.update(_search_details(search_report))
            if not record["evidenceValid"]:
                record["verdict"] = "FAIL"
        colour = "green" if record["verdict"] == "PASS" else "red"
        where = f"  (output in {rest_log})" if tier == "rest" and record["verdict"] != "PASS" else ""
        console.print(f"[{colour}]{tier} smoke: {record['verdict']}[/{colour}]{where}")
        return record

    # The REST tier runs beside the browser, split and search tiers. Its sign-ins and theirs take
    # one machine-wide authentication lock, and its fixtures are its own. Finish its initial
    # clean-stack inventory before browser fixtures exist. The split and search tiers hold
    # the split tier's lock, so they still run one after the other.
    started = clock()
    results = {}
    with rest_log.open("w", encoding="utf-8") as log, ThreadPoolExecutor(max_workers=1) as background:
        console.print(f"[bold]rest smoke[/bold], beside the other tiers: {' '.join(commands['rest'])}")
        console.print(f"  output in {rest_log}")
        rest = background.submit(run, "rest", stdout=log, stderr=subprocess.STDOUT)
        _wait_for_rest_preflight(rest, rest_preflight)
        for tier in TIERS:
            if tier != "rest":
                console.print(f"[bold]{tier} smoke[/bold]: {' '.join(commands[tier])}")
                results[tier] = run(tier)
        results["rest"] = rest.result()
    tiers = {tier: results[tier] for tier in TIERS}

    # Judged again now that every tier has finished, since nothing stopped the stack moving meanwhile.
    changes = changes_during_run(home, repositories, runner, heads=heads, dirty=tree_dirty,
                                 processes=processes, frontend_build=frontend_build)
    if changes:
        console.print("[red]The stack or its sources changed while the tiers ran:[/red]")
        for change in changes:
            console.print(f"  - {change}", markup=False)
    finished = clock()
    verdict = "PASS" if all(t["verdict"] == "PASS" for t in tiers.values()) and not changes else "FAIL"
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "verdict": verdict,
        "startedAt": _stamp(started),
        "finishedAt": _stamp(finished),
        "durationSeconds": round((finished - started).total_seconds(), 1),
        "sourcesDigest": digest,
        "sources": dict(sorted(heads.items())),
        "dirty": sorted(dirty),
        "frontendBuild": frontend_build,
        "changedDuringRun": changes,
        "tiers": tiers,
    }
    payload = json.dumps(report, indent=2) + "\n"
    (reports / f"{digest}.json").write_text(payload, encoding="utf-8")
    (reports / "latest.json").write_text(payload, encoding="utf-8")
    colour = "green" if verdict == "PASS" else "red"
    console.print(f"[{colour}]Whole-stack smoke: {verdict}[/{colour}]  ({len(heads)} sources, digest {digest})")
    console.print(f"Recorded: {reports / f'{digest}.json'}")
    return 0 if verdict == "PASS" else 1


def allowed_skips(path: Path) -> set[tuple[str, str, str, str]]:
    """The skipped REST checks the committed allowlist accepts, each with the one reason it names.

    A skipped check proves nothing about the deployment, and its identity is the same as a passed
    one's, so the inventory comparison cannot tell them apart. Only a skip this file names, for the
    reason it names, is evidence of a run that did what it could.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("skips") if isinstance(payload, dict) and payload.get("version") == 1 else None
    if not isinstance(entries, list):
        raise ValueError(f"invalid committed REST skip allowlist: {path}")
    allowed = set()
    for entry in entries:
        values = tuple(entry.get(key) for key in ("suite", "section", "name", "reason")) \
            if isinstance(entry, dict) else ()
        if len(values) != 4 or not all(isinstance(value, str) and value for value in values):
            raise ValueError(f"invalid committed REST skip allowlist: {path}")
        allowed.add(values)
    return allowed


# The repository whose inventory the REST tier runs, and the inventory's path inside it.
REST_INVENTORY = (E2E / "rest" / "expected-checks.json").relative_to("cedar-development")
RECORD_NAME = re.compile(r"^[0-9a-f]{16}\.json$")


def retired_checks(path: Path) -> set[str]:
    """The REST check identities the committed list retires, each of which must carry a reason."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("checks") if isinstance(payload, dict) and payload.get("version") == 1 else None
    if not isinstance(entries, list):
        raise ValueError(f"invalid committed list of retired REST checks: {path}")
    retired = set()
    for entry in entries:
        values = tuple(entry.get(key) for key in ("suite", "section", "name", "reason")) \
            if isinstance(entry, dict) else ()
        if len(values) != 4 or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError(f"invalid committed list of retired REST checks: {path}")
        retired.add("\x1f".join(values[:3]))
    return retired


def last_passing_development_head(reports: Path) -> tuple[str, str] | None:
    """The cedar-development head of the latest passing record with clean REST evidence, and when it finished."""
    latest = None
    for path in reports.glob("*.json"):
        if not RECORD_NAME.match(path.name):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("verdict") != "PASS":
            continue
        rest = (record.get("tiers") or {}).get("rest")
        head = (record.get("sources") or {}).get("cedar-development")
        finished = record.get("finishedAt")
        if (not isinstance(rest, dict) or rest.get("evidenceValid") is not True
                or "cedar-development" in (record.get("dirty") or [])
                or not isinstance(head, str) or not isinstance(finished, str)):
            continue
        if latest is None or finished > latest[1]:
            latest = (head, finished)
    return latest


def dropped_rest_checks(cedar_home, inventory_path: Path, runner=subprocess.run) -> tuple[list[str], str | None]:
    """Checks the last passing run executed that the inventory has lost without being retired.

    Identities are names, so a commit can delete a failing check with its inventory entry and every
    later run will match the shorter inventory. The last passing run's inventory, read from the commit
    it tested, is the memory that comparison lacks. Returns the unretired identities, and a reason
    when the comparison could not be made.
    """
    home = _home(cedar_home)
    previous = last_passing_development_head(reports_dir(home))
    if previous is None:
        return [], None
    head, finished = previous
    code, out, err = _capture(runner, ["git", "show", f"{head}:{REST_INVENTORY.as_posix()}"],
                              cwd=home / "cedar-development")
    try:
        if code != 0:
            raise ValueError(err.strip() or f"git show exited {code}")
        before = json.loads(out)["checks"]
        now = json.loads(inventory_path.read_text(encoding="utf-8"))["checks"]
        retired = retired_checks(inventory_path.with_name("retired-checks.json"))
        if not isinstance(before, list) or not isinstance(now, list):
            raise ValueError("an inventory's checks are not a list")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [], (f"cannot compare the REST inventory with the one the passing run at {finished} "
                    f"tested at cedar-development {head[:8]}: {error}")
    remaining = Counter(now)
    dropped = []
    for identity in before:
        if remaining[identity] > 0:
            remaining[identity] -= 1
        elif identity not in retired:
            dropped.append(identity)
    return dropped, None


# The MCP server whose built jar the REST tier's mcp suite runs from its working tree.
REST_MCP = "cedar-artifact-rest-mcp"


def mcp_source_problem(sources) -> str | None:
    """Why the REST report cannot say which MCP commit its mcp suite ran, or None when it can.

    The MCP servers are outside the train, so only the REST report records their heads. A jar built
    from a tree with uncommitted changes holds code no commit does.
    """
    entry = sources.get(REST_MCP) if isinstance(sources, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("head"), str) \
            or not re.fullmatch(r"[0-9a-f]{40}", entry["head"]) or not isinstance(entry.get("dirty"), bool):
        return f"the REST report records no commit of {REST_MCP}, whose jar its mcp suite ran"
    if entry["dirty"]:
        return f"{REST_MCP} had uncommitted changes when its jar ran in the REST tier's mcp suite"
    return None


def _rest_details(rest_report: Path, inventory_path: Path, allowlist_path: Path | None = None) -> dict:
    """Require complete REST evidence, independently of the subprocess exit code.

    A skipped check counts only when the committed allowlist beside the inventory names it with the
    reason the run recorded. The report must also name the commit of the MCP server the mcp suite ran.
    """
    details = {"report": str(rest_report), "evidenceValid": False}
    try:
        payload = json.loads(rest_report.read_text(encoding="utf-8"))
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        allowed = allowed_skips(allowlist_path or inventory_path.with_name("allowed-skips.json"))
        if not isinstance(payload, dict):
            raise ValueError("REST evidence is not an object")
        expected = inventory["checks"]
        if (inventory.get("version") != 1 or not isinstance(expected, list) or not expected
                or any(not isinstance(key, str) or len(key.split("\x1f")) != 3
                       or not all(key.split("\x1f")) for key in expected)):
            raise ValueError("invalid committed REST inventory")
        suites = list(dict.fromkeys(key.split("\x1f")[0] for key in expected
                                    if not key.startswith("runner\x1f")))
        checks = payload["checks"]
        if not isinstance(checks, list):
            raise ValueError("REST checks are not a list")
        actual = ["\x1f".join(check[key] for key in ("suite", "section", "name")) for check in checks]
        # Compare sequences, preserving multiplicity: a repeated check label is not a set member.
        matched = actual == expected and payload.get("inventoryMatched") is True
        statuses = Counter(check["status"] for check in checks)
        unallowed = ["\x1f".join(check[key] for key in ("suite", "section", "name")) for check in checks
                     if check["status"] == "skipped"
                     and (check["suite"], check["section"], check["name"], check.get("reason")) not in allowed]
        counts = payload["counts"]
        counts_valid = (isinstance(counts, dict)
                        and all(type(counts.get(key)) is int and counts[key] >= 0
                                for key in ("passed", "failed", "skipped", "total"))
                        and counts["total"] == len(checks)
                        and all(counts[key] == statuses[key] for key in ("passed", "failed", "skipped"))
                        and counts["passed"] > 0 and counts["failed"] == 0
                        and set(statuses) <= {"passed", "skipped"})
        started = dt.datetime.fromisoformat(payload["startedAt"].replace("Z", "+00:00"))
        finished = dt.datetime.fromisoformat(payload["finishedAt"].replace("Z", "+00:00"))
        mcp_problem = mcp_source_problem(payload.get("mcpSources"))
        valid = (payload.get("schemaVersion") == 1 and payload.get("verdict") == "PASS"
                 and payload.get("interrupted") is False
                 and isinstance(payload.get("runId"), str) and bool(payload["runId"].strip())
                 and started.tzinfo is not None and finished.tzinfo is not None and finished >= started
                 and payload.get("selectedSuites") == suites and matched and counts_valid and not unallowed
                 and mcp_problem is None)
        details.update({"runId": payload.get("runId"), "runnerVerdict": payload.get("verdict"),
                        "counts": counts, "inventoryMatched": matched, "evidenceValid": valid,
                        "unallowedSkips": unallowed, "mcpSources": payload.get("mcpSources")})
        if unallowed:
            details["reportError"] = (f"{len(unallowed)} REST check(s) skipped that rest/allowed-skips.json does not "
                                      f"allow with the reason given, such as {unallowed[0].replace(chr(31), ' / ')}")
        elif mcp_problem:
            details["reportError"] = mcp_problem
        elif not valid:
            details["reportError"] = "REST evidence is incomplete, failed, or does not cover the committed inventory"
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        details["reportError"] = str(error)
    return details


def _browser_details(directory: Path, inventory_path: Path) -> dict:
    """Require every browser script to report each step the committed inventory names, in order.

    An exit code says nothing about which checks a script made, so a script that stopped checking
    halfway and exited 0 passed. Each script now writes the steps it completed, and the record
    holds the run to browser-steps.json.
    """
    details = {"reports": str(directory), "evidenceValid": False}
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        scripts = inventory.get("scripts") if isinstance(inventory, dict) and inventory.get("version") == 1 else None
        if (not isinstance(scripts, dict) or not scripts
                or not all(isinstance(steps, list) and steps and all(isinstance(step, str) and step for step in steps)
                           for steps in scripts.values())):
            raise ValueError(f"invalid committed browser step inventory: {inventory_path}")
        reported, problems = {}, []
        for script, expected in scripts.items():
            try:
                payload = json.loads((directory / f"{script}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                problems.append(f"{script} wrote no readable step report ({error})")
                continue
            steps = payload.get("steps") if isinstance(payload, dict) else None
            reported[script] = {"verdict": payload.get("verdict") if isinstance(payload, dict) else None,
                                "steps": len(steps) if isinstance(steps, list) else None,
                                "failedStep": payload.get("failedStep") if isinstance(payload, dict) else None}
            if payload.get("schemaVersion") != 1 or payload.get("script") != script or payload.get("verdict") != "PASS":
                problems.append(f"{script} reported {payload.get('verdict')!r}"
                                + (f" in step {payload['failedStep']}" if payload.get("failedStep") else ""))
            elif steps != expected:
                missing = [step for step in expected if step not in (steps or [])]
                problems.append(f"{script} completed other steps than browser-steps.json names"
                                + (f", without {missing[0]}" if missing else ""))
        details.update({"scripts": reported, "evidenceValid": not problems})
        if problems:
            details["reportError"] = "; ".join(problems)
    except (OSError, ValueError, AttributeError, TypeError) as error:
        details["reportError"] = str(error)
    return details


def _split_evidence(split_report: Path, inventory_path: Path, exit_code: int | None = None) -> dict:
    """Require the split tier to have run every unit and every committed journey scenario.

    The tier runner's verdict and exit code say that nothing it ran failed, not that it ran what it
    should: every unit must have passed, nothing may be left unrun, and the journeys must have run
    exactly the scenarios journey-scenarios.json commits, each journey at least once.
    """
    evidence = {"evidenceValid": False}
    if exit_code == SPLIT_LOCK_REFUSED:
        return evidence
    try:
        payload = json.loads(split_report.read_text(encoding="utf-8"))
        committed = json.loads(inventory_path.read_text(encoding="utf-8"))
        if not isinstance(committed, dict) or not committed:
            raise ValueError(f"invalid committed journey inventory: {inventory_path}")
        expected = {key: sorted(scenario["id"] for scenario in scenarios) for key, scenarios in committed.items()}
        units, journeys = payload["units"], payload["journeyResults"]
        ran: dict[str, list[list[str]]] = {}
        for journey in journeys:
            if journey.get("outcome") == "pass":
                ran.setdefault(journey.get("journeyKey"), []).append(
                    sorted(scenario["id"] for scenario in journey.get("scenarios") or []))
        unmatched = sorted(key for key in expected
                           if not ran.get(key) or any(ids != expected[key] for ids in ran[key]))
        unexpected = sorted(str(key) for key in ran if key not in expected)
        problems = []
        if payload.get("verdict") != "PASS" or payload.get("inventoryMode") != "verified":
            problems.append(f"the split runner reported {payload.get('verdict')!r} in "
                            f"{payload.get('inventoryMode')!r} inventory mode")
        if payload.get("notRun") != [] or payload.get("failures") != []:
            problems.append("the split runner left units unrun or failed")
        if not isinstance(units, list) or not units or any(unit.get("outcome") != "pass" for unit in units):
            problems.append("not every split unit passed")
        if unmatched or unexpected:
            problems.append("the journeys ran other scenarios than journey-scenarios.json commits, such as "
                            + (unmatched or unexpected)[0])
        evidence.update({"evidenceValid": not problems, "inventoryMatched": not (unmatched or unexpected),
                         "journeys": len(ran)})
        if problems:
            evidence["evidenceError"] = "; ".join(problems)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        evidence["evidenceError"] = str(error)
    return evidence


def _search_details(path: Path) -> dict:
    """Require every matrix family and successful cleanup, independently of the exit code."""
    details = {"report": str(path), "evidenceValid": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("search evidence is not an object")
        expected = []
        cells, counts = payload["cells"], payload["matrixCells"]
        for family in ("content", "access", "paging", "protocol", "lifecycle", "journey"):
            rows = cells[family]
            if (not isinstance(rows, list) or type(counts[family]) is not int
                    or counts[family] <= 0 or counts[family] != len(rows)):
                raise ValueError(f"invalid {family} inventory")
            expected.extend(rows if family == "lifecycle" else
                            [("journey/" if family == "journey" else "") + row["id"] for row in rows])
        if any(not isinstance(key, str) or not key for key in expected) or len(set(expected)) != len(expected):
            raise ValueError("search inventory contains invalid or duplicate identities")
        checks = payload["checks"]
        actual = [check["id"] for check in checks]
        matched = len(actual) == len(expected) and set(actual) == set(expected)
        failed = [check["id"] for check in checks if check.get("status") != "pass"]
        completion = payload["completion"]
        valid = (payload.get("tier") == "all"
                 and payload.get("status") == "IMPLEMENTED_CASES_PASS_WITH_DECLARED_GAPS"
                 and bool(payload.get("finishedAt")) and bool(payload.get("runToken"))
                 and bool(payload.get("harnessDigest")) and bool(payload.get("corpusDigest"))
                 and payload.get("cleanupErrors") == [] and payload.get("browserErrors") == []
                 and not payload.get("fatal") and matched and not failed
                 and completion.get("complete") is True
                 and all(completion.get(key) == [] for key in ("missing", "unexpected", "failed")))
        details.update({"evidenceValid": valid, "runToken": payload.get("runToken"),
                        "runnerVerdict": payload.get("status"), "inventoryMatched": matched,
                        "counts": {"total": len(checks), "pass": len(checks) - len(failed), "fail": len(failed),
                                   "families": counts},
                        "failed": failed, "completion": completion,
                        "fatal": payload.get("fatal"), "cleanupErrors": payload.get("cleanupErrors"),
                        "browserErrors": payload.get("browserErrors"),
                        "harnessDigest": payload.get("harnessDigest"), "corpusDigest": payload.get("corpusDigest"),
                        "performance": payload.get("performance"), "declaredGaps": payload.get("exclusions")})
        if not valid:
            details["reportError"] = "search evidence is incomplete, failed, or does not cover the full REST and browser matrix"
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        details["reportError"] = str(error)
    return details


def _split_details(split_report: Path, exit_code: int | None = None) -> dict:
    """Which scripts and steps of the split tier failed, how long each lane and script took, and the
    machine's load at start and end, from the tier runner's own report. Its full step timeline stays in
    that file; the record keeps the five slowest steps.

    A report of schema 1 comes from a sequential run and names one failing script at most. Schema 2
    adds the lanes, each with its journeys and scripts, its time, its exit code and the step it failed
    in, and every failure rather than the first alone. Schema 3 has the lanes take their units from one
    queue, so each lane lists the units it ran, with their timings, its busy and idle time, any unit
    that overran its deadline and what the sweep after that kill removed; the report also names the
    lock's holder and the deadline settings. The fields of the earlier schemas keep their meaning, and
    from schema 2 on they describe the first failure.

    A run that found another run holding the lock exits with SPLIT_LOCK_REFUSED and writes no report,
    so whatever file is at the report's path belongs to some other run and is not read."""
    if exit_code == SPLIT_LOCK_REFUSED:
        return {"report": str(split_report), "lockRefused": True,
                "reportError": "another split tier run held the lock, so this one ran nothing and wrote no report"}
    try:
        payload = json.loads(split_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"report": str(split_report), "reportError": str(error)}
    steps = [step for step in payload.get("steps") or [] if isinstance(step.get("seconds"), (int, float))]
    details = {
        "report": str(split_report),
        "schemaVersion": payload.get("schemaVersion"),
        "runnerVerdict": payload.get("verdict"),
        "failingScript": payload.get("failingScript"),
        "failingStep": payload.get("failingStep"),
        "failure": (payload.get("failure") or "")[:500] or None,
        "scripts": [{key: script.get(key) for key in ("name", "lane", "seconds", "exitCode") if key in script}
                    for script in payload.get("scripts") or []],
        "notRun": payload.get("notRun"),
        "machine": payload.get("machine"),
        "slowestSteps": sorted(steps, key=lambda step: -step["seconds"])[:5],
    }
    queued = isinstance(payload.get("schemaVersion"), int) and payload["schemaVersion"] >= 3
    if isinstance(payload.get("lanes"), list):
        lane_keys = ("name", "keys", "scripts", "seconds", "exitCode", "failingScript", "failingStep", "notRun")
        if queued:
            lane_keys += ("busySeconds", "overheadSeconds", "idleSeconds", "timeout")
        details["sequential"] = payload.get("sequential")
        details["generalLanes"] = payload.get("generalLanes")
        details["durationSeconds"] = payload.get("durationSeconds")
        details["checks"] = payload.get("checks")
        details["lanes"] = []
        for lane in payload["lanes"]:
            if not isinstance(lane, dict):
                continue
            entry = {key: lane.get(key) for key in lane_keys}
            if queued:
                entry["units"] = [{key: unit.get(key) for key in ("name", "seconds", "outcome")}
                                  for unit in lane.get("units") or [] if isinstance(unit, dict)]
                entry["sweep"] = _sweep_summary(lane.get("sweep"))
            details["lanes"].append(entry)
        details["failures"] = [{
            "lane": failure.get("lane"),
            "script": failure.get("script"),
            "step": failure.get("step"),
            "failure": (failure.get("message") or "")[:500] or None,
            **({"timeout": failure["timeout"]} if queued and failure.get("timeout") else {}),
        } for failure in payload.get("failures") or [] if isinstance(failure, dict)]
    if queued:
        lock = payload.get("lock") if isinstance(payload.get("lock"), dict) else None
        details["lock"] = None if lock is None else {
            "path": lock.get("path"),
            "holder": lock.get("holder"),
            "replaced": lock.get("replaced"),
        }
        details["unitTimeout"] = payload.get("unitTimeout")
        details["timeouts"] = [timeout for timeout in payload.get("timeouts") or [] if isinstance(timeout, dict)]
        details["sweeps"] = [_sweep_summary(sweep) for sweep in payload.get("sweeps") or [] if isinstance(sweep, dict)]
        details["evidenceDirectory"] = payload.get("evidenceDirectory")
    return details


def _sweep_summary(sweep) -> dict | None:
    """What a sweep after a kill removed and put back, counted rather than listed; the runner's report lists it."""
    if not isinstance(sweep, dict):
        return None
    counted = {key: len(sweep.get(key) or []) for key in
               ("removed", "alreadyGone", "failed", "secondUser", "restored", "alreadyRestored")}
    root = sweep.get("root") if isinstance(sweep.get("root"), dict) else None
    return {
        "lane": sweep.get("lane"),
        "unit": sweep.get("unit"),
        "root": None if root is None else {key: root.get(key) for key in ("name", "how", "deleted") if key in root},
        **counted,
        "notRemoved": [{key: item.get(key) for key in ("collection", "id", "status")}
                       for item in sweep.get("failed") or [] if isinstance(item, dict)],
        "notRestored": [{key: item.get(key) for key in ("restore", "label", "as", "status")}
                        for item in sweep.get("notRestored") or [] if isinstance(item, dict)],
        **({"error": sweep["error"]} if sweep.get("error") else {}),
    }


def read_report(cedar_home, expected: Mapping[str, str]) -> dict:
    """The run recorded for exactly these heads, else the latest run so the gate can say what moved."""
    reports = reports_dir(cedar_home)
    exact = reports / f"{sources_digest(expected)}.json"
    latest = reports / "latest.json"
    path = exact if exact.exists() else latest
    if not path.exists():
        raise SmokeGateError(f"no whole-stack smoke run is recorded; {REMEDY}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SmokeGateError(f"the recorded smoke run at {path} cannot be read: {error}") from error
    if not isinstance(report, dict):
        raise SmokeGateError(f"the recorded smoke run at {path} is not a report")
    return report


def evaluate(report: Mapping, expected: Mapping[str, str]) -> list[str]:
    """Every reason the recorded run does not stand for this source, and nothing when it does."""
    findings: list[str] = []
    if report.get("schemaVersion") != SCHEMA_VERSION:
        return [f"the recorded smoke run has schema {report.get('schemaVersion')!r}, "
                f"not {SCHEMA_VERSION}; {REMEDY}"]
    when = report.get("finishedAt", "an unknown time")

    sources = report.get("sources") or {}
    unrecorded = [name for name in expected if name not in sources]
    moved = [name for name, head in expected.items()
             if name in sources and sources[name] != head]
    if unrecorded:
        findings.append(
            f"the smoke run at {when} recorded no source for {_names(unrecorded)}")
    if moved:
        if len(moved) <= SUMMARY_LIMIT:
            for name in moved:
                findings.append(
                    f"{name}: the smoke run at {when} tested {sources[name][:8]}, "
                    f"this source is {expected[name][:8]}")
        else:
            findings.append(
                f"the smoke run at {when} tested other commits of {len(moved)} repositories "
                f"({_names(moved)})")

    if report.get("changedDuringRun"):
        findings.append(f"the stack or its sources changed while the smoke run at {when} ran: "
                        + "; ".join(report["changedDuringRun"][:SUMMARY_LIMIT]))
    dirty = [name for name in report.get("dirty") or [] if name in expected]
    if dirty:
        findings.append(f"{_names(dirty)} had uncommitted changes when the smoke ran")

    tiers = report.get("tiers") or {}
    for tier in TIERS:
        record = tiers.get(tier)
        if not isinstance(record, dict):
            findings.append(f"the smoke run at {when} recorded no {tier} tier")
            continue
        if record.get("verdict") != "PASS":
            findings.append(f"the {tier} smoke was {record.get('verdict', 'unrecorded')} at "
                            f"{record.get('finishedAt', when)}")
        elif tier == "rest" and record.get("inventoryMatched") is False:
            findings.append("the REST smoke ran a different check inventory than the committed one")
        elif tier == "rest" and record.get("droppedChecks"):
            findings.append(f"the REST inventory dropped {len(record['droppedChecks'])} check(s) the last passing "
                            "run executed without naming them in rest/retired-checks.json")
        elif tier == "rest" and record.get("unallowedSkips"):
            findings.append(f"the REST smoke skipped {len(record['unallowedSkips'])} check(s) that "
                            "rest/allowed-skips.json does not allow")
        elif tier == "rest" and record.get("evidenceValid") is not True:
            findings.append("the REST smoke has no validated complete check evidence")
        elif tier == "search" and record.get("evidenceValid") is not True:
            findings.append("the search smoke has no complete matrix and cleanup evidence")
        elif tier in ("browser", "split") and record.get("evidenceValid") is not True:
            findings.append(f"the {tier} smoke has no evidence that it ran every step its inventory names")
    return findings


def findings_for(cedar_home, expected: Mapping[str, str]) -> list[str]:
    """Read and judge in one step, turning an unanswerable gate into its one finding."""
    try:
        report = read_report(cedar_home, expected)
    except SmokeGateError as error:
        return [str(error)]
    return evaluate(report, expected)


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:SUMMARY_LIMIT])
    if len(names) > SUMMARY_LIMIT:
        shown += f" and {len(names) - SUMMARY_LIMIT} more"
    return shown

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

The record must also answer for the jars. The controller's own `current` column establishes that
no process predates its jar, which is a narrower question than it looks: a jar can itself predate
the commit the record names, and every row reads `current` while it does. A run therefore refuses
to start while any service is stale or unhealthy, and equally while any deployed jar was written
before its repository's develop head, because the evidence would otherwise certify code the stack
is not running.
"""

from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from collections import Counter
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


class SmokeGateError(ValueError):
    """The gate could not be answered from what is recorded."""


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
    before its repository's develop head cannot contain that head, and a run recorded against
    it certifies code the stack is not running. That is the failure this module's own note
    called the operator's discipline; it is cheap enough to ask.
    """
    findings = []
    for service in services:
        repository = f"cedar-{service}-server"
        root = _home(cedar_home) / repository
        if not (root / ".git").exists():
            continue
        jar = application_jar(cedar_home, service)
        if jar is None:
            continue
        code, head, _detail = _capture(
            runner, ["git", "log", "-1", "--format=%ct %H", "refs/heads/develop"], cwd=root)
        if code != 0 or not head.strip():
            continue
        committed, _, revision = head.strip().partition(" ")
        try:
            committed_at = int(committed)
        except ValueError:
            continue
        if jar.stat().st_mtime < committed_at:
            findings.append(
                f"{service} was built before its source: its jar predates {repository} "
                f"develop {revision[:8]}"
            )
    return findings


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
    one fails, so one command yields one complete answer.
    """
    if rest_workers is not None and (type(rest_workers) is not int or not 1 <= rest_workers <= 4):
        raise ValueError("REST workers must be an integer from 1 to 4")
    if split_lanes is not None and (type(split_lanes) is not int or not 1 <= split_lanes <= 4):
        raise ValueError("split lanes must be an integer from 1 to 4")
    if split_timeout_cap is not None and (
            type(split_timeout_cap) is not int
            or not SPLIT_TIMEOUT_CAP_RANGE[0] <= split_timeout_cap <= SPLIT_TIMEOUT_CAP_RANGE[1]):
        raise ValueError("the split tier's timeout cap must be an integer number of seconds from "
                         f"{SPLIT_TIMEOUT_CAP_RANGE[0]} to {SPLIT_TIMEOUT_CAP_RANGE[1]}")
    home = _home(cedar_home or invocation_environment().get("CEDAR_HOME"))
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
    if head_problems:
        console.print("[red]Source heads cannot be recorded:[/red]")
        for problem in head_problems:
            console.print(f"  - {problem}", markup=False)
        return 1
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
    split_report = reports / f"split-smoke-{digest}.json"
    # A refused or killed run must never inherit an earlier search verdict at the same source heads.
    search_report = reports / f"search-smoke-{digest}-{uuid.uuid4().hex}.json"
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

    started = clock()
    tiers = {}
    for tier in TIERS:
        console.print(f"[bold]{tier} smoke[/bold]: {' '.join(commands[tier])}")
        tier_started = clock()
        try:
            result = runner(commands[tier], cwd=str(e2e), env=environment, check=False)
            code = result.returncode
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
            record.update(_rest_details(rest_report, e2e / "rest" / "expected-checks.json"))
            if not record["evidenceValid"]:
                record["verdict"] = "FAIL"
        if tier == "split":
            record.update(_split_details(split_report, code))
        if tier == "search":
            record.update(_search_details(search_report))
            if not record["evidenceValid"]:
                record["verdict"] = "FAIL"
        tiers[tier] = record
        colour = "green" if record["verdict"] == "PASS" else "red"
        console.print(f"[{colour}]{tier} smoke: {record['verdict']}[/{colour}]")

    finished = clock()
    verdict = "PASS" if all(t["verdict"] == "PASS" for t in tiers.values()) else "FAIL"
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "verdict": verdict,
        "startedAt": _stamp(started),
        "finishedAt": _stamp(finished),
        "durationSeconds": round((finished - started).total_seconds(), 1),
        "sourcesDigest": digest,
        "sources": dict(sorted(heads.items())),
        "dirty": sorted(dirty),
        "tiers": tiers,
    }
    payload = json.dumps(report, indent=2) + "\n"
    (reports / f"{digest}.json").write_text(payload, encoding="utf-8")
    (reports / "latest.json").write_text(payload, encoding="utf-8")
    colour = "green" if verdict == "PASS" else "red"
    console.print(f"[{colour}]Whole-stack smoke: {verdict}[/{colour}]  ({len(heads)} sources, digest {digest})")
    console.print(f"Recorded: {reports / f'{digest}.json'}")
    return 0 if verdict == "PASS" else 1


def _rest_details(rest_report: Path, inventory_path: Path) -> dict:
    """Require complete REST evidence, independently of the subprocess exit code."""
    details = {"report": str(rest_report), "evidenceValid": False}
    try:
        payload = json.loads(rest_report.read_text(encoding="utf-8"))
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
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
        valid = (payload.get("schemaVersion") == 1 and payload.get("verdict") == "PASS"
                 and payload.get("interrupted") is False
                 and isinstance(payload.get("runId"), str) and bool(payload["runId"].strip())
                 and started.tzinfo is not None and finished.tzinfo is not None and finished >= started
                 and payload.get("selectedSuites") == suites and matched and counts_valid)
        details.update({"runId": payload.get("runId"), "runnerVerdict": payload.get("verdict"),
                        "counts": counts, "inventoryMatched": matched, "evidenceValid": valid})
        if not valid:
            details["reportError"] = "REST evidence is incomplete, failed, or does not cover the committed inventory"
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        details["reportError"] = str(error)
    return details


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
        elif tier == "rest" and record.get("evidenceValid") is not True:
            findings.append("the REST smoke has no validated complete check evidence")
        elif tier == "search" and record.get("evidenceValid") is not True:
            findings.append("the search smoke has no complete matrix and cleanup evidence")
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

"""`cedarcli ci run`: run repositories' CI at exact commits and record the result."""
from __future__ import annotations

import datetime as dt
import platform
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from org.metadatacenter.local_ci import evidence
from org.metadatacenter.local_ci.coverage import (
    RepositoryCoverage, repository_coverage, runs_in_linux)
from org.metadatacenter.local_ci.errors import LocalCIError
from org.metadatacenter.local_ci.executor import JobRunner, JobResult, base_environment
from org.metadatacenter.local_ci.registry import Registry, load_registry
from org.metadatacenter.local_ci.workflows import git

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(name, threading.Lock())


@dataclass
class RepositoryOutcome:
    repository: str
    revision: str
    state: str  # 'passed', 'failed', 'uncovered', 'error', 'no-ci'
    detail: str = ""
    record_path: str = ""
    status: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def resolve_revision(cedar_home: Path, repository: str, explicit: str | None) -> str:
    root = cedar_home / repository
    if not (root / ".git").exists():
        raise LocalCIError(f"{repository} is not checked out under {cedar_home}")
    if explicit:
        git(root, "cat-file", "-e", f"{explicit}^{{commit}}")
        return git(root, "rev-parse", f"{explicit}^{{commit}}")
    git(root, "fetch", "-q", "origin", "develop")
    return git(root, "rev-parse", "refs/remotes/origin/develop")


def toolchain_environment(source: dict) -> tuple[dict, list[str]]:
    """Java 17 and Node 24.22.1 first on PATH, as the release toolchain resolves them."""
    from org.metadatacenter.release_support.toolchain import ToolchainResolver
    environment = dict(source)
    notes = ToolchainResolver(environment).resolve()
    return environment, notes


def _versions(environment: dict) -> dict:
    def capture(*command):
        try:
            result = subprocess.run(list(command), text=True, capture_output=True, check=False,
                                    env=environment)
        except OSError:
            return ""
        return (result.stdout or result.stderr).strip().splitlines()[0] if (
            result.stdout or result.stderr).strip() else ""
    return {"node": capture("node", "--version"), "npm": capture("npm", "--version"),
            "java": capture("java", "-version"), "git": capture("git", "--version"),
            "os": platform.platform(), "machine": platform.machine()}


def _ordered(legs):
    """Legs whose needs have finished first, keeping the workflows' own order otherwise."""
    pending = list(legs)
    order = []
    done: dict[tuple, int] = {}
    totals: dict[tuple, int] = {}
    for coverage in legs:
        identity = (coverage.leg.workflow.path, coverage.leg.job_id)
        totals[identity] = totals.get(identity, 0) + 1
    while pending:
        progressed = False
        for coverage in list(pending):
            workflow = coverage.leg.workflow.path
            if all(done.get((workflow, need), 0) == totals.get((workflow, need), 0)
                   for need in coverage.leg.needs):
                order.append(coverage)
                pending.remove(coverage)
                identity = (workflow, coverage.leg.job_id)
                done[identity] = done.get(identity, 0) + 1
                progressed = True
        if not progressed:
            raise LocalCIError("the workflows' needs form a cycle or name a missing job")
    return order


def _needs_context(results: dict, coverage) -> dict:
    context = {}
    for need in coverage.leg.needs:
        legs = results.get((coverage.leg.workflow.path, need), [])
        outcome = "skipped" if legs and all(r.conclusion == "skipped" for r in legs) else (
            "failure" if any(r.conclusion == "failure" for r in legs) else "success")
        outputs = {}
        for result in legs:
            outputs.update(result.outputs)
        context[need] = {"result": outcome, "outputs": outputs}
    return context


def _run_legs(job_runner, legs, repository, reporter, parallel) -> list:
    """Every leg once its needs have finished, several at a time, as GitHub schedules them."""
    from concurrent.futures import FIRST_COMPLETED, wait
    ordered = _ordered(legs)
    totals: dict[tuple, int] = {}
    for coverage in ordered:
        identity = (coverage.leg.workflow.path, coverage.leg.job_id)
        totals[identity] = totals.get(identity, 0) + 1
    results: dict[tuple, list[JobResult]] = {}
    by_leg: dict[int, JobResult] = {}
    pending = list(enumerate(ordered))
    running = {}
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        while pending or running:
            for index, coverage in list(pending):
                workflow = coverage.leg.workflow.path
                if all(len(results.get((workflow, need), [])) == totals.get((workflow, need), 0)
                       for need in coverage.leg.needs):
                    pending.remove((index, coverage))
                    reporter(f"{repository}: {coverage.leg.leg}")
                    future = pool.submit(job_runner.run, coverage, _needs_context(results, coverage))
                    running[future] = (index, coverage)
            if not running:
                raise LocalCIError("the workflows' needs form a cycle or name a missing job")
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                index, coverage = running.pop(future)
                result = future.result()
                results.setdefault((coverage.leg.workflow.path, coverage.leg.job_id), []).append(result)
                by_leg[index] = result
                reporter(f"{repository}: {coverage.leg.leg} {result.conclusion} in "
                         f"{result.seconds / 60:.1f} min")
    return [by_leg[index] for index in sorted(by_leg)]


def _docker_ready(environment: dict) -> bool:
    try:
        result = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], text=True,
                                capture_output=True, check=False, env=environment, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def run_repository(cedar_home: Path, repository: str, revision: str, coverage: RepositoryCoverage,
                   registry: Registry, *, source_environment: dict, post: bool,
                   reporter: Callable[[str], None], runner=subprocess.run,
                   parallel: int = 4) -> RepositoryOutcome:
    environment, notes = toolchain_environment(source_environment)
    for note in notes:
        reporter(f"Toolchain: {note}")
    clean = base_environment(source_environment, environment)
    from org.metadatacenter.util.NexusCredentials import environment_with_nexus_credentials
    credentials = environment_with_nexus_credentials(dict(source_environment))
    secrets = {name: credentials[name] for name in ("BMIR_NEXUS_USERNAME", "BMIR_NEXUS_PASSWORD")
               if credentials.get(name)}
    requirements = sorted({requirement for leg in coverage.legs if leg.config
                           for requirement in leg.config.requires}
                          | ({"docker"} if any(runs_in_linux(leg.leg, leg.config)
                                               for leg in coverage.legs) else set()))
    if "docker" in requirements and not _docker_ready(clean):
        return RepositoryOutcome(repository, revision, "error",
                                 "its jobs need Docker, and Docker is not running")
    root = cedar_home / repository
    parents = git(root, "rev-list", "--parents", "-n", "1", revision).split()[1:]
    started = dt.datetime.now(dt.timezone.utc)
    run_name = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{repository}-{revision[:8]}"
    run_directory = evidence.root(source_environment) / "runs" / run_name
    run_directory.mkdir(parents=True, exist_ok=True)
    job_runner = JobRunner(
        cedar_home=cedar_home, repository=repository, revision=revision,
        parent=parents[0] if parents else None, run_directory=run_directory, environment=clean,
        secrets=secrets, maven_repository=evidence.root(source_environment) / "maven" / "repository",
        reporter=reporter)
    exclusive = sorted({leg.config.exclusive for leg in coverage.legs
                        if leg.config and leg.config.exclusive})
    locks = [_lock(name) for name in exclusive]
    for lock in locks:
        lock.acquire()
    try:
        finished = _run_legs(job_runner, coverage.legs, repository, reporter, parallel)
    finally:
        for lock in reversed(locks):
            lock.release()
    failed = [result.leg for result in finished if result.conclusion == "failure"]
    record = {
        "schemaVersion": evidence.SCHEMA_VERSION,
        "repository": repository,
        "revision": revision,
        "tree": git(root, "rev-parse", f"{revision}^{{tree}}"),
        "parent": parents[0] if parents else None,
        "conclusion": "failure" if failed else "success",
        "covered": coverage.covered,
        "registrySha256": registry.digest(repository),
        "statusContext": registry.status_context,
        "inputs": dict(sorted(job_runner.inputs_used.items())),
        "toolchain": _versions(clean),
        "cedarCliRevision": git(Path(__file__).resolve().parents[3], "rev-parse", "HEAD", check=False).strip(),
        "host": evidence.host(),
        "startedAt": started.isoformat(),
        "finishedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "runDirectory": str(run_directory),
        "jobs": [_with_log_digest(result.as_record()) for result in finished],
    }
    path = evidence.write_record(record, source_environment)
    outcome = RepositoryOutcome(repository, revision, "failed" if failed else "passed",
                                f"{len(finished)} job(s); failed: {', '.join(failed)}" if failed
                                else f"{len(finished)} job(s) passed", str(path))
    if post:
        try:
            outcome.status = evidence.post_status(record, registry.status_context, runner,
                                                  dict(source_environment))
        except LocalCIError as error:
            outcome.detail += f"; {error}"
    return outcome


def _with_log_digest(record: dict) -> dict:
    log = Path(record.get("log", ""))
    if log.is_file():
        record["logSha256"] = evidence.file_sha256(log)
    record["called"] = [_with_log_digest(child) for child in record.get("called", [])]
    if not record["called"]:
        record.pop("called")
    return record


def run(cedar_home, repositories: list[str], *, revisions: dict[str, str] | None = None,
        jobs: int = 2, post: bool = True, reporter: Callable[[str], None] = print,
        source_environment: dict | None = None) -> list[RepositoryOutcome]:
    import os
    cedar_home = Path(cedar_home)
    source_environment = dict(os.environ if source_environment is None else source_environment)
    registry = load_registry(cedar_home)
    revisions = revisions or {}
    plans = []
    outcomes: list[RepositoryOutcome] = []
    for repository in repositories:
        try:
            revision = resolve_revision(cedar_home, repository, revisions.get(repository))
        except LocalCIError as error:
            outcomes.append(RepositoryOutcome(repository, revisions.get(repository, ""), "error", str(error)))
            continue
        coverage = repository_coverage(cedar_home, repository, revision, registry)
        if not coverage.runs_ci and not coverage.problems:
            outcomes.append(RepositoryOutcome(repository, revision, "no-ci",
                                              "no workflow runs on a push of this commit to develop"))
            continue
        if not coverage.covered:
            outcomes.append(RepositoryOutcome(repository, revision, "uncovered",
                                              "GitHub still answers for this repository",
                                              reasons=coverage.reasons()))
            continue
        plans.append((repository, revision, coverage))

    def execute(plan):
        repository, revision, coverage = plan
        try:
            return run_repository(cedar_home, repository, revision, coverage, registry,
                                  source_environment=source_environment, post=post, reporter=reporter)
        except LocalCIError as error:
            return RepositoryOutcome(repository, revision, "error", str(error))

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        outcomes.extend(pool.map(execute, plans))
    return outcomes


def parse_revisions(values: list[str] | None) -> dict[str, str]:
    revisions = {}
    for value in values or []:
        repository, separator, revision = value.partition("=")
        if not separator or not re.fullmatch(r"[0-9a-fA-F]{7,40}|[A-Za-z0-9_./-]+", revision.strip()):
            raise LocalCIError(f"--revision expects <repository>=<commit>, not {value!r}")
        revisions[repository.strip()] = revision.strip()
    return revisions

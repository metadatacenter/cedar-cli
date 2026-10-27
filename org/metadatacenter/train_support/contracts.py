"""Cross-repository contract workflows, and whether their newest run still covers their consumers.

A contract workflow lives in one repository and checks out others at a moving branch, so a run
answers only for the consumer heads it checked out. A push to a consumer does not run the contract,
and nothing then reports on it until the contract repository itself changes, which can be the
release's own push. Release 2.9.30 stopped exactly there: the term picker's new bundle check had
never met the design tokens' consumer contract, whose last run predated it.
"""
from __future__ import annotations
from dataclasses import dataclass
import base64
import binascii
import json
import re
import subprocess

from org.metadatacenter.github_ci import DEVELOP_EVENTS, GREEN_CONCLUSIONS, run_url
from org.metadatacenter.util.InvocationContext import invocation_environment

# Each workflow that checks out other repositories and judges them against this one.
CONTRACT_WORKFLOWS = (
    ("cedar-design-tokens", "consumers.yml"),
    ("cedar-design-tokens", "token-readers.yml"),
)

# A scheduled run checks out the consumers as a pushed one does.
CONTRACT_EVENTS = DEVELOP_EVENTS | {"schedule"}


@dataclass(frozen=True)
class ContractVerdict:
    repository: str
    workflow: str
    state: str
    detail: str
    url: str = ""

    STATES = ("green", "stale", "red", "pending", "missing", "error")

    @property
    def blocks_a_train(self) -> bool:
        return self.state != "green"

    @property
    def dispatchable(self) -> bool:
        """A contract a fresh run can settle: one no run covers yet."""
        return self.state in {"stale", "missing"}

    @property
    def dispatch_command(self) -> list[str]:
        return ["gh", "workflow", "run", self.workflow,
                "--repo", f"metadatacenter/{self.repository}", "--ref", "develop"]


def contract_consumers(text: str) -> list[tuple[str, str]]:
    """The repositories and branches a contract workflow checks out, in the order it names them.

    The design tokens' workflows name their consumers three ways: as a job matrix of `repo:`
    entries, as a shell loop over repository names, and as one clone of an explicit branch.
    Matrix and loop entries check out `develop`.
    """
    consumers: list[tuple[str, str]] = []

    def add(repository: str, branch: str) -> None:
        if (repository, branch) not in consumers:
            consumers.append((repository, branch))

    for repository in re.findall(r"^\s*-\s*repo:\s*([\w.-]+)\s*$", text, re.MULTILINE):
        add(repository, "develop")
    loop = re.search(r"for repo in ([^;]+);\s*do", text)
    if loop:
        for repository in re.findall(r"[\w.-]+", loop.group(1)):
            add(repository, "develop")
    for branch, repository in re.findall(
            r"git clone[^\n]*--branch (\S+) https://github\.com/metadatacenter/([\w.-]+?)(?:\.git)?\s",
            text):
        add(repository, branch)
    return consumers


def _gh_json(route: str, runner, environment) -> dict:
    try:
        result = runner(["gh", "api", route], text=True, capture_output=True, check=False,
                        env=environment)
    except OSError as error:
        raise ValueError(f"cannot read {route}: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise ValueError(f"cannot read {route}: {detail[-1] if detail else f'exit {result.returncode}'}")
    try:
        value = json.loads(result.stdout or "")
    except json.JSONDecodeError as error:
        raise ValueError(f"{route} answered malformed JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{route} answered {type(value).__name__}, not an object")
    return value


def _pushed_at(repository: str, branch: str, runner, environment) -> tuple[str, str]:
    """The branch head, and the time it was pushed, as the creation of its first push run.

    A commit's own date can precede its push by days. Its push runs are created when it arrives,
    so the earliest of them dates the push; a repository with no workflow falls back to the commit.
    """
    commit = _gh_json(f"repos/metadatacenter/{repository}/commits/{branch}", runner, environment)
    revision = commit.get("sha", "")
    committed = commit.get("commit", {}).get("committer", {}).get("date", "")
    runs = _gh_json(f"repos/metadatacenter/{repository}/actions/runs?head_sha={revision}&per_page=50",
                    runner, environment).get("workflow_runs", [])
    created = [run["created_at"] for run in runs
               if isinstance(run, dict) and run.get("event") == "push" and run.get("created_at")]
    return revision, min(created) if created else committed


def contract_verdict(repository: str, workflow: str, *, runner=None, environment=None) -> ContractVerdict:
    """Whether the newest run of one contract at its develop head is green and covers its consumers."""
    runner = runner or subprocess.run
    environment = invocation_environment() if environment is None else environment
    try:
        head = _gh_json(f"repos/metadatacenter/{repository}/commits/develop", runner,
                        environment).get("sha", "")
        content = _gh_json(
            f"repos/metadatacenter/{repository}/contents/.github/workflows/{workflow}?ref={head}",
            runner, environment).get("content", "")
        consumers = contract_consumers(base64.b64decode(content).decode("utf-8"))
        runs = _gh_json(
            f"repos/metadatacenter/{repository}/actions/workflows/{workflow}/runs"
            "?branch=develop&per_page=20", runner, environment).get("workflow_runs", [])
        at_head = [run for run in runs
                   if isinstance(run, dict) and run.get("head_sha") == head
                   and run.get("event") in CONTRACT_EVENTS
                   and not (run.get("status") == "completed" and run.get("conclusion") == "cancelled")]
        if not at_head:
            return ContractVerdict(repository, workflow, "missing",
                                   f"{workflow} has no run at develop {head[:8]}")
        latest = max(at_head, key=lambda run: run.get("created_at") or "")
        url = run_url(latest)
        if latest.get("status") != "completed":
            return ContractVerdict(repository, workflow, "pending",
                                   f"{workflow} is {latest.get('status') or 'pending'}", url)
        if latest.get("conclusion") not in GREEN_CONCLUSIONS:
            return ContractVerdict(repository, workflow, "red",
                                   f"{workflow} concluded {latest.get('conclusion')}", url)
        started = latest.get("created_at") or ""
        later = []
        for consumer, branch in consumers:
            revision, pushed = _pushed_at(consumer, branch, runner, environment)
            if pushed > started:
                later.append(f"{consumer} {revision[:8]}")
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as error:
        return ContractVerdict(repository, workflow, "error", f"{workflow}: {error}")
    if later:
        return ContractVerdict(repository, workflow, "stale",
                               f"{workflow} last ran {started}, before pushes to " + ", ".join(later),
                               url)
    return ContractVerdict(repository, workflow, "green",
                           f"{workflow} covers all {len(consumers)} consumers", url)


def contract_survey(*, runner=None, environment=None) -> list[ContractVerdict]:
    return [contract_verdict(repository, workflow, runner=runner, environment=environment)
            for repository, workflow in CONTRACT_WORKFLOWS]


def dispatch_contracts(verdicts: list[ContractVerdict], *, runner=None, environment=None) -> list[str]:
    """Start a run of every contract no run yet covers; return a line for each."""
    runner = runner or subprocess.run
    environment = invocation_environment() if environment is None else environment
    lines = []
    for verdict in verdicts:
        if not verdict.dispatchable:
            continue
        result = runner(verdict.dispatch_command, text=True, capture_output=True, check=False,
                        env=environment)
        outcome = "dispatched" if result.returncode == 0 else (
            "not dispatched: " + ((result.stderr or result.stdout or "").strip().splitlines() or
                                  [f"exit {result.returncode}"])[-1])
        lines.append(f"{verdict.repository} {verdict.workflow}: {outcome}")
    return lines

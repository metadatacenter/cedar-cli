"""Which workflow jobs a push to `develop` runs, read from the exact commit.

Local CI never keeps its own copy of what a repository's CI does. It reads the workflows the commit
carries, works out which of them a push of that commit to `develop` triggers, and expands each job's
matrix into the legs GitHub would run. The registry then only says which legs local CI covers and
which of their steps it skips or substitutes.
"""
from __future__ import annotations

import fnmatch
import itertools
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from org.metadatacenter.local_ci.errors import LocalCIError

WORKFLOW_DIRECTORY = ".github/workflows"


def _yaml():
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - exercised only on an outdated virtualenv
        raise LocalCIError(
            "local CI reads workflow YAML with PyYAML, which this cedar-cli environment lacks; "
            "run: $CEDAR_HOME/cedar-cli/.venv/bin/python3 -m pip install -r "
            "$CEDAR_HOME/cedar-cli/requirements.txt") from error
    return yaml


def git(repository, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(repository), *args], text=True,
                            capture_output=True, check=False)
    if check and result.returncode:
        raise LocalCIError(f"git {' '.join(args)} failed in {repository}: "
                           f"{(result.stderr or result.stdout).strip()}")
    return result.stdout if not check else result.stdout.rstrip("\n")


def load_document(text: str, label: str) -> dict:
    """One workflow file. YAML 1.1 reads the key `on` as true, which this undoes."""
    try:
        document = _yaml().safe_load(text)
    except Exception as error:  # noqa: BLE001 - every PyYAML failure means the same thing here
        raise LocalCIError(f"{label} is not valid YAML: {error}") from error
    if not isinstance(document, dict):
        raise LocalCIError(f"{label} is not a workflow")
    if True in document and "on" not in document:
        document["on"] = document.pop(True)
    return document


@dataclass(frozen=True)
class Workflow:
    path: str
    document: dict

    @property
    def file(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def name(self) -> str:
        return str(self.document.get("name") or self.file)


def workflows_at(repository, revision: str) -> list[Workflow]:
    listing = git(repository, "ls-tree", "--name-only", f"{revision}:{WORKFLOW_DIRECTORY}", check=False)
    files = sorted(name for name in listing.splitlines() if name.endswith((".yml", ".yaml")))
    result = []
    for name in files:
        path = f"{WORKFLOW_DIRECTORY}/{name}"
        text = git(repository, "show", f"{revision}:{path}")
        result.append(Workflow(path, load_document(text, f"{repository.name}:{path}")))
    return result


def changed_files(repository, revision: str) -> list[str] | None:
    """The files the push of `revision` changes against its first parent, or None for a root."""
    parents = git(repository, "rev-list", "--parents", "-n", "1", revision).split()[1:]
    if not parents:
        return None
    output = git(repository, "diff", "--name-only", "--no-renames", parents[0], revision)
    return [line for line in output.splitlines() if line]


def _glob(pattern: str, value: str) -> bool:
    """GitHub's filter globs: `*` stops at `/`, `**` does not."""
    expression = ""
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if pattern.startswith("**", index):
            expression += ".*"
            index += 2
            continue
        if character == "*":
            expression += "[^/]*"
        elif character == "?":
            expression += "[^/]"
        elif character in "+":
            expression += re.escape(character)
        else:
            expression += re.escape(character) if character not in "[]!" else character
        index += 1
    try:
        return re.fullmatch(expression, value) is not None
    except re.error:
        return fnmatch.fnmatchcase(value, pattern)


def _filter(patterns: list[str], value: str) -> bool:
    """Evaluate a filter list in order; a later `!pattern` excludes what an earlier one included."""
    matched = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if _glob(pattern[1:], value):
                matched = False
        elif _glob(pattern, value):
            matched = True
    return matched


def push_trigger(document: dict):
    triggers = document.get("on")
    if isinstance(triggers, str):
        return {} if triggers == "push" else None
    if isinstance(triggers, list):
        return {} if "push" in triggers else None
    if isinstance(triggers, dict) and "push" in triggers:
        value = triggers["push"]
        return value if isinstance(value, dict) else {}
    return None


def triggered_by_push(document: dict, branch: str, changed: list[str] | None) -> bool:
    """Whether pushing a commit with these changed files to `branch` starts the workflow."""
    trigger = push_trigger(document)
    if trigger is None:
        return False
    branches = trigger.get("branches")
    ignored = trigger.get("branches-ignore")
    if branches is not None:
        if not _filter([str(item) for item in branches], branch):
            return False
    elif ignored is not None:
        if _filter([str(item) for item in ignored], branch):
            return False
    elif trigger.get("tags") is not None or trigger.get("tags-ignore") is not None:
        return False
    paths = trigger.get("paths")
    paths_ignored = trigger.get("paths-ignore")
    if changed is None:
        return True
    if paths is not None:
        return any(_filter([str(item) for item in paths], path) for path in changed)
    if paths_ignored is not None:
        return any(not _filter([str(item) for item in paths_ignored], path) for path in changed)
    return True


@dataclass(frozen=True)
class JobLeg:
    """One job of one workflow, for one combination of its matrix."""

    workflow: Workflow
    job_id: str
    definition: dict
    matrix: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """`<workflow file>:<job id>`, the name the registry gives the job."""
        return f"{self.workflow.file}:{self.job_id}"

    @property
    def leg(self) -> str:
        if not self.matrix:
            return self.key
        values = ", ".join(f"{name}={json.dumps(value) if not isinstance(value, str) else value}"
                           for name, value in sorted(self.matrix.items()))
        return f"{self.key} [{values}]"

    @property
    def needs(self) -> list[str]:
        value = self.definition.get("needs") or []
        return [value] if isinstance(value, str) else [str(item) for item in value]


def matrix_legs(strategy) -> list[dict]:
    """Expand a strategy's matrix the way GitHub does: product, then exclude, then include."""
    if not isinstance(strategy, dict) or "matrix" not in strategy:
        return [{}]
    matrix = strategy["matrix"]
    if not isinstance(matrix, dict):
        raise LocalCIError("a matrix given as an expression cannot be expanded locally")
    axes = {name: values for name, values in matrix.items() if name not in ("include", "exclude")}
    for name, values in axes.items():
        if not isinstance(values, list):
            raise LocalCIError(f"the matrix axis {name} is not a list")
    combinations = [dict(zip(axes, values)) for values in itertools.product(*axes.values())] \
        if axes else []
    for excluded in matrix.get("exclude") or []:
        combinations = [combination for combination in combinations
                        if not all(combination.get(key) == value for key, value in excluded.items())]
    originals = [dict(combination) for combination in combinations]
    extra = []
    for included in matrix.get("include") or []:
        extended = False
        for index, original in enumerate(originals):
            overlap = {key: value for key, value in included.items() if key in axes}
            if all(original.get(key) == value for key, value in overlap.items()):
                conflicts = any(key in combinations[index] and key not in axes
                                and combinations[index][key] != value
                                for key, value in included.items())
                if not conflicts:
                    combinations[index].update(included)
                    extended = True
        if not extended:
            extra.append(dict(included))
    legs = combinations + extra
    return legs or [{}]


def job_legs(workflow: Workflow) -> list[JobLeg]:
    jobs = workflow.document.get("jobs") or {}
    if not isinstance(jobs, dict):
        raise LocalCIError(f"{workflow.path} has no jobs mapping")
    legs = []
    for job_id, definition in jobs.items():
        if not isinstance(definition, dict):
            raise LocalCIError(f"{workflow.path} job {job_id} is not a mapping")
        for matrix in matrix_legs(definition.get("strategy")):
            legs.append(JobLeg(workflow, str(job_id), definition, matrix))
    return legs


def step_key(step: dict, index: int) -> str:
    """The name the registry uses for a step: its name, else its id, else its position."""
    if step.get("name"):
        return str(step["name"])
    if step.get("id"):
        return f"id:{step['id']}"
    return f"#{index + 1}"

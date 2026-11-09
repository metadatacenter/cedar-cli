"""Whether local CI can answer for a commit: the structural drift check.

A job is covered when the registry names it and every step in it is one local CI runs exactly as
GitHub would, or one the registry skips or substitutes with a reason. A step that publishes, an
action local CI does not implement, a secret it does not hold, an expression it cannot evaluate, a
job container or a service: each makes the job uncovered, and with it the repository, so a gate
waits for GitHub there instead of accepting a weaker answer. A workflow change that adds any of
these is caught the same way, at the commit that makes it.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from org.metadatacenter.local_ci.errors import LocalCIError
from org.metadatacenter.local_ci.expressions import (
    UnsupportedExpression, expressions_in, referenced_contexts, uses_status_function, _tokens)
from org.metadatacenter.local_ci.registry import JobConfig, Registry
from org.metadatacenter.local_ci.workflows import (
    JobLeg, Workflow, changed_files, git, job_legs, load_document, step_key, triggered_by_push,
    workflows_at)

SUPPORTED_ACTIONS = frozenset({
    "actions/checkout", "actions/setup-java", "actions/setup-node", "actions/setup-python",
    "actions/cache", "actions/cache/restore", "actions/cache/save",
    "actions/upload-artifact", "actions/download-artifact",
})
SECRETS = frozenset({"BMIR_NEXUS_USERNAME", "BMIR_NEXUS_PASSWORD"})
CONTEXTS = frozenset({"github", "env", "matrix", "steps", "needs", "inputs", "runner", "job",
                      "strategy", "secrets"})
GITHUB_PROPERTIES = frozenset({
    "event_name", "ref", "ref_name", "ref_type", "sha", "repository", "repository_owner",
    "workspace", "event", "run_id", "run_number", "run_attempt", "server_url", "api_url", "actor",
    "base_ref", "head_ref", "job", "workflow", "action", "triggering_actor", "event_path",
})
REQUIREMENTS = frozenset({"docker"})
SHELLS = frozenset({None, "bash", "sh"})
PUBLISHES = re.compile(
    r"\bdeploy\b(?=[^\n]*(?:-DskipTests|--settings|mvnw?))|(?:mvnw?|\./mvnw)[^\n]*\bdeploy\b"
    r"|\bnpm\s+publish\b|\bdocker\s+push\b|\bgh\s+(?:release|issue)\b"
    r"|\bgh\s+api\b[^\n]*(?:-X|--method)\s*(?:POST|PUT|PATCH|DELETE)\b|\bgit\s+push\b")
SYSTEM_PACKAGES = re.compile(r"(?:^|[\s;&|(])(?:sudo|apt-get|apt|dpkg)(?=\s)")
REUSABLE = re.compile(r"^metadatacenter/(?P<repository>[A-Za-z0-9_.-]+)/(?P<path>\.github/workflows/"
                      r"[A-Za-z0-9_.-]+\.ya?ml)@(?P<ref>[A-Za-z0-9_./-]+)$")


@dataclass
class LegCoverage:
    leg: JobLeg
    config: JobConfig | None
    problems: list[str] = field(default_factory=list)
    called: list["LegCoverage"] = field(default_factory=list)
    called_workflow: tuple | None = None

    @property
    def covered(self) -> bool:
        return not self.problems and all(child.covered for child in self.called)

    def reasons(self) -> list[str]:
        own = [f"{self.leg.leg}: {problem}" for problem in self.problems]
        return own + [reason for child in self.called for reason in child.reasons()]


@dataclass
class RepositoryCoverage:
    repository: str
    revision: str
    legs: list[LegCoverage]
    problems: list[str] = field(default_factory=list)

    @property
    def runs_ci(self) -> bool:
        return bool(self.legs)

    @property
    def covered(self) -> bool:
        return self.runs_ci and not self.problems and all(leg.covered for leg in self.legs)

    def reasons(self) -> list[str]:
        return self.problems + [reason for leg in self.legs for reason in leg.reasons()]


def python_interpreter(version: str) -> str | None:
    """A local Python whose version is the one a workflow asks setup-python for."""
    version = str(version).strip()
    for candidate in (f"python{version}", f"/opt/homebrew/opt/python@{version}/bin/python{version}",
                      f"/usr/local/opt/python@{version}/bin/python{version}"):
        found = shutil.which(candidate) if "/" not in candidate else (
            candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def _expression_problems(value, label: str) -> list[str]:
    problems = []
    for expression in expressions_in(value):
        try:
            _tokens(expression)
            references = referenced_contexts(expression)
        except UnsupportedExpression as error:
            problems.append(f"{label} has an expression local CI cannot read ({error})")
            continue
        for reference in sorted(references):
            context, _, member = reference.partition(".")
            if context not in CONTEXTS:
                problems.append(f"{label} reads the context {context!r}, which local CI does not have")
            elif context == "secrets" and member and member not in SECRETS:
                problems.append(f"{label} reads the secret {member}, which local CI does not hold")
            elif context == "github" and member and member not in GITHUB_PROPERTIES:
                problems.append(f"{label} reads github.{member}, which local CI does not provide")
    return problems


def _condition_problems(value, label: str) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    text = str(value).strip()
    inner = text[3:-2].strip() if text.startswith("${{") and text.endswith("}}") else text
    try:
        uses_status_function(inner)
    except UnsupportedExpression as error:
        return [f"{label} has a condition local CI cannot read ({error})"]
    return _expression_problems("${{ " + inner + " }}", label)


def _action(uses: str) -> str:
    return uses.split("@", 1)[0].strip()


def step_problems(step: dict, key: str, config: JobConfig) -> list[str]:
    if key in config.skip:
        return []
    label = f"step {key!r}"
    problems = []
    if key in config.substitute:
        replacement = config.substitute[key]
        step = {**step, "run": replacement["run"]}
        step.pop("uses", None)
    problems.extend(_condition_problems(step.get("if"), label))
    problems.extend(_expression_problems({k: v for k, v in step.items() if k != "if"}, label))
    if "uses" in step:
        action = _action(str(step["uses"]))
        if action.startswith("./") or action not in SUPPORTED_ACTIONS:
            problems.append(f"{label} uses {step['uses']}, which local CI does not implement")
        inputs = step.get("with") or {}
        if action == "actions/setup-java":
            version = str(inputs.get("java-version", "")).strip()
            if version != "17":
                problems.append(f"{label} asks for Java {version or '(unspecified)'}; local CI has Java 17")
        if action == "actions/setup-node":
            version = str(inputs.get("node-version", "")).strip()
            if not version and not inputs.get("node-version-file"):
                problems.append(f"{label} names no Node version")
        if action == "actions/setup-python":
            version = str(inputs.get("python-version", "")).strip()
            if "${{" not in version and not python_interpreter(version):
                problems.append(f"{label} asks for Python {version or '(unspecified)'}, "
                                "which this machine does not have")
        if action == "actions/checkout":
            repository = str(inputs.get("repository", "")).strip()
            if repository and "${{" not in repository and not repository.startswith("metadatacenter/"):
                problems.append(f"{label} checks out {repository}, outside metadatacenter")
            if inputs.get("submodules"):
                problems.append(f"{label} checks out submodules, which local CI does not")
    elif "run" in step:
        script = str(step["run"])
        if step.get("shell") not in SHELLS:
            problems.append(f"{label} runs under the shell {step.get('shell')!r}, which local CI does not")
        if PUBLISHES.search(script):
            problems.append(f"{label} publishes or writes to GitHub; skip it in the registry")
        if SYSTEM_PACKAGES.search(script):
            problems.append(f"{label} installs system packages with sudo, apt or dpkg; "
                            "skip or substitute it in the registry")
    else:
        problems.append(f"{label} neither runs a command nor uses an action")
    return problems


def leg_coverage(cedar_home: Path, repository: str, leg: JobLeg, config: JobConfig | None,
                 *, depth: int = 0) -> LegCoverage:
    coverage = LegCoverage(leg, config)
    if config is None:
        coverage.problems.append(f"the local CI registry names no job {leg.key} for {repository}")
        return coverage
    if config.uncovered:
        coverage.problems.append(f"not covered locally: {config.uncovered}")
        return coverage
    if config.omit:
        if not _publication_job(leg.definition):
            coverage.problems.append("the registry omits a job that does more than publish")
        return coverage
    reason = config.leg_uncovered(leg.matrix)
    if reason:
        coverage.problems.append(f"not covered locally: {reason}")
        return coverage
    definition = leg.definition
    for requirement in config.requires:
        if requirement not in REQUIREMENTS:
            coverage.problems.append(f"the registry requires {requirement!r}, which local CI cannot provide")
    if definition.get("container"):
        coverage.problems.append("the job runs in a container, which local CI does not provide yet")
    if definition.get("services"):
        coverage.problems.append("the job needs service containers, which local CI does not provide yet")
    coverage.problems.extend(_condition_problems(definition.get("if"), "the job condition"))
    for name in ("env", "outputs", "defaults", "timeout-minutes", "continue-on-error"):
        if name in definition:
            coverage.problems.extend(_expression_problems(definition[name], f"the job's {name}"))
    if "uses" in definition:
        coverage.problems.extend(_expression_problems(definition.get("with") or {}, "the call's inputs"))
        match = REUSABLE.match(str(definition["uses"]).strip())
        if not match:
            coverage.problems.append(f"the job calls {definition['uses']}, which local CI cannot resolve")
            return coverage
        if depth > 2:
            coverage.problems.append("reusable workflows nest too deeply")
            return coverage
        called_repository = Path(cedar_home) / match.group("repository")
        if not (called_repository / ".git").exists():
            coverage.problems.append(f"the called workflow's repository {match.group('repository')} "
                                     "is not checked out")
            return coverage
        revision = resolve_ref(called_repository, match.group("ref"))
        try:
            text = git(called_repository, "show", f"{revision}:{match.group('path')}")
        except LocalCIError as error:
            coverage.problems.append(str(error))
            return coverage
        called = Workflow(match.group("path"), load_document(text, str(definition["uses"])))
        coverage.called_workflow = (match.group("repository"), revision, called)
        for child in job_legs(called):
            coverage.called.append(leg_coverage(
                cedar_home, repository, child, config.for_called(child.job_id), depth=depth + 1))
        return coverage
    steps = definition.get("steps")
    if not isinstance(steps, list) or not steps:
        coverage.problems.append("the job has no steps")
        return coverage
    keys = [step_key(step, index) for index, step in enumerate(steps)]
    for name in list(config.skip) + list(config.substitute):
        if name not in keys:
            coverage.problems.append(f"the registry names a step {name!r} the job no longer has")
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            coverage.problems.append(f"step {index + 1} is not a mapping")
            continue
        coverage.problems.extend(step_problems(step, keys[index], config))
    return coverage


def _publication_job(definition: dict) -> bool:
    """A job is omitted only when it verifies nothing: every command it runs publishes."""
    commands = [str(step.get("run")) for step in definition.get("steps") or []
                if isinstance(step, dict) and step.get("run")]
    return bool(commands) and any(PUBLISHES.search(command) for command in commands)


def resolve_ref(repository: Path, ref: str) -> str:
    """The commit a ref names in a local checkout: a full SHA as itself, a branch at origin."""
    if re.fullmatch(r"[0-9a-f]{40}", ref):
        git(repository, "cat-file", "-e", f"{ref}^{{commit}}")
        return ref
    for candidate in (f"refs/remotes/origin/{ref}", f"refs/tags/{ref}", ref):
        result = git(repository, "rev-parse", "--verify", "-q", f"{candidate}^{{commit}}", check=False)
        if result.strip():
            return result.strip()
    raise LocalCIError(f"{repository.name} has no ref {ref!r}; fetch it first")


def repository_coverage(cedar_home, repository: str, revision: str, registry: Registry,
                        *, branch: str = "develop") -> RepositoryCoverage:
    """What a push of `revision` to `branch` runs in `repository`, and whether local CI covers it."""
    root = Path(cedar_home) / repository
    coverage = RepositoryCoverage(repository, revision, [])
    if not (root / ".git").exists():
        coverage.problems.append(f"{repository} is not checked out under {cedar_home}")
        return coverage
    try:
        workflows = workflows_at(root, revision)
        changed = changed_files(root, revision)
    except LocalCIError as error:
        coverage.problems.append(str(error))
        return coverage
    for workflow in workflows:
        if not triggered_by_push(workflow.document, branch, changed):
            continue
        try:
            legs = job_legs(workflow)
        except LocalCIError as error:
            coverage.problems.append(f"{workflow.file}: {error}")
            continue
        for leg in legs:
            try:
                config = registry.job(repository, leg.key)
            except LocalCIError as error:
                coverage.problems.append(str(error))
                continue
            coverage.legs.append(leg_coverage(cedar_home, repository, leg, config))
    if coverage.legs and registry.entry(repository) is None:
        coverage.problems.insert(0, f"the local CI registry has no entry for {repository}")
    return coverage

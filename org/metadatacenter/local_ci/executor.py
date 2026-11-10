"""Run one workflow job on this machine the way a GitHub runner would.

Each job gets a fresh workspace, an environment holding nothing from the shell that started it
beyond the tools themselves, and the workflow's own steps in order. Local CI implements the setup
actions the workflows use (checkout from the local checkouts at exact commits, Java, Node and Python
setup, caching as a no-op, artifacts handed from one job to the next) and runs every `run:` step
with the condition, environment and working directory the workflow gives it.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from org.metadatacenter.local_ci.coverage import LegCoverage, python_interpreter, resolve_ref
from org.metadatacenter.local_ci.errors import LocalCIError
from org.metadatacenter.local_ci.expressions import (
    UnsupportedExpression, condition, interpolate, to_string)
from org.metadatacenter.local_ci.workflows import git, step_key

REQUIRED_NODE = "v24.22.1"
DEFAULT_JOB_MINUTES = 360
SAFE_VARIABLES = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "SHELL", "DOCKER_HOST",
                  "DOCKER_CONFIG", "SSH_AUTH_SOCK", "TZ")
SYSTEM_PATH = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/bin", "/bin",
               "/usr/sbin", "/sbin", "/Applications/Docker.app/Contents/Resources/bin")
LEG_DIRECTORY_LENGTH = 120


def leg_directory(label: str) -> str:
    """The directory one job leg runs in: readable, and distinct for every leg of a run.

    A leg's label spells out its matrix, so legs of one job can differ only near the end of a long
    label, as shards do. A name that has to be shortened keeps a digest of the whole label.
    """
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")
    if len(slug) <= LEG_DIRECTORY_LENGTH:
        return slug
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
    return f"{slug[:LEG_DIRECTORY_LENGTH - len(digest) - 1].rstrip('-')}-{digest}"


@dataclass
class StepResult:
    name: str
    conclusion: str
    seconds: float = 0.0
    detail: str = ""


@dataclass
class JobResult:
    leg: str
    conclusion: str
    seconds: float
    log: str
    steps: list[StepResult] = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    called: list["JobResult"] = field(default_factory=list)

    def as_record(self) -> dict:
        record = {"leg": self.leg, "conclusion": self.conclusion, "seconds": round(self.seconds, 1),
                  "log": self.log,
                  "steps": [{"name": step.name, "conclusion": step.conclusion,
                             "seconds": round(step.seconds, 1),
                             **({"detail": step.detail} if step.detail else {})}
                            for step in self.steps]}
        if self.called:
            record["called"] = [child.as_record() for child in self.called]
        return record


def base_environment(source: dict, toolchain: dict) -> dict:
    """What a clean runner offers: the tools, a home, and nothing a developer shell exported."""
    environment = {name: source[name] for name in SAFE_VARIABLES if source.get(name)}
    environment.setdefault("LANG", "en_US.UTF-8")
    paths = [entry for entry in toolchain.get("PATH", "").split(os.pathsep) if entry]
    for directory in SYSTEM_PATH:
        if directory not in paths:
            paths.append(directory)
    environment["PATH"] = os.pathsep.join(paths)
    if toolchain.get("JAVA_HOME"):
        environment["JAVA_HOME"] = toolchain["JAVA_HOME"]
    environment["CI"] = "true"
    environment["TERM"] = "dumb"
    return environment


def _parse_command_file(path: Path) -> dict:
    """NAME=value lines and NAME<<DELIMITER blocks, as GITHUB_ENV and GITHUB_OUTPUT hold them."""
    values = {}
    if not path.exists():
        return values
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delimiter = line.split("<<", 1)
            collected = []
            while index < len(lines) and lines[index] != delimiter:
                collected.append(lines[index])
                index += 1
            index += 1
            values[name.strip()] = "\n".join(collected)
        elif "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value
    return values


class _Log:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a", encoding="utf-8")
        self.lock = threading.Lock()

    def write(self, text: str) -> None:
        with self.lock:
            self.handle.write(text)
            self.handle.flush()

    def close(self) -> None:
        self.handle.close()


class JobRunner:
    """Runs the job legs of one repository at one commit, in one local CI run directory."""

    def __init__(self, *, cedar_home: Path, repository: str, revision: str, parent: str | None,
                 run_directory: Path, environment: dict, secrets: dict, maven_repository: Path,
                 reporter: Callable[[str], None] = lambda line: None,
                 command_runner=subprocess.Popen, clock=time.monotonic, run_id: str | None = None):
        self.cedar_home = Path(cedar_home)
        self.repository = repository
        self.revision = revision
        self.parent = parent
        self.run_directory = Path(run_directory)
        self.environment = environment
        self.secrets = secrets
        self.maven_repository = Path(maven_repository)
        self.reporter = reporter
        self.command_runner = command_runner
        self.clock = clock
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.artifacts = self.run_directory / "artifacts"
        self.inputs_used: dict[str, str] = {repository: revision}
        self.fetched: set[str] = set()
        self.lock = threading.Lock()

    # -- contexts ---------------------------------------------------------------------------

    def github_context(self, workspace: Path, job_id: str, workflow_name: str) -> dict:
        before = self.parent or "0" * 40
        return {
            "event_name": "push", "ref": "refs/heads/develop", "ref_name": "develop",
            "ref_type": "branch", "sha": self.revision,
            "repository": f"metadatacenter/{self.repository}", "repository_owner": "metadatacenter",
            "workspace": str(workspace), "run_id": self.run_id, "run_number": "1", "run_attempt": "1",
            "server_url": "https://github.com", "api_url": "https://api.github.com",
            "actor": "local-ci", "triggering_actor": "local-ci", "base_ref": "", "head_ref": "",
            "job": job_id, "workflow": workflow_name, "action": "",
            "event_path": str(workspace.parent / "event.json"),
            "event": {"before": before, "after": self.revision, "ref": "refs/heads/develop",
                      "head_commit": {"id": self.revision},
                      "repository": {"full_name": f"metadatacenter/{self.repository}",
                                     "name": self.repository, "default_branch": "develop"}},
        }

    # -- running ----------------------------------------------------------------------------

    def run(self, coverage: LegCoverage, needs: dict, *, inputs: dict | None = None,
            workflow_env: dict | None = None, label: str | None = None) -> JobResult:
        leg = coverage.leg
        config = coverage.config
        slug = leg_directory(label or leg.leg)
        job_root = self.run_directory / "jobs" / slug
        try:
            job_root.mkdir(parents=True)
        except FileExistsError as error:
            raise LocalCIError(f"two legs of {self.repository} share the directory {slug}; "
                               "a leg never runs in another leg's workspace") from error
        workspace = job_root / "workspace"
        temporary = job_root / "tmp"
        meta = job_root / "meta"
        for directory in (workspace, temporary, meta):
            directory.mkdir(parents=True, exist_ok=True)
        log = _Log(job_root / "job.log")
        started = self.clock()
        github = self.github_context(workspace, leg.job_id, leg.workflow.name)
        (job_root / "event.json").write_text(json.dumps(github["event"], indent=2))
        context = {
            "github": github, "matrix": dict(leg.matrix), "needs": needs, "inputs": inputs or {},
            "secrets": dict(self.secrets), "steps": {}, "env": {},
            "runner": {"os": "macOS", "arch": "ARM64", "temp": str(temporary),
                       "tool_cache": str(temporary / "toolcache"), "name": "local-ci"},
            "job": {"status": "success"},
            "strategy": {"fail-fast": True, "job-index": 0, "job-total": 1, "max-parallel": 1},
        }
        definition = leg.definition
        result = JobResult(leg.leg, "success", 0.0, str(log.path))
        try:
            document_env = workflow_env if workflow_env is not None else self._env_values(
                leg.workflow.document.get("env"), context)
            context["env"] = dict(document_env)
            if config.omit:
                result.conclusion = "skipped"
                result.steps.append(StepResult("job", "skipped", 0.0, f"omitted: {config.omit}"))
                log.write(f"== {leg.leg}: omitted ({config.omit})\n")
                return result
            if not condition(definition.get("if"), context,
                             lambda name: self._job_status(name, needs)):
                result.conclusion = "skipped"
                log.write(f"== {leg.leg}: skipped by its condition\n")
                return result
            job_env = {**document_env, **self._env_values(definition.get("env"), context),
                       **config.env}
            context["env"] = dict(job_env)
            if "uses" in definition:
                result.called = self._run_called(coverage, context, job_env, slug)
                result.conclusion = "success" if all(
                    child.conclusion in ("success", "skipped") for child in result.called) else "failure"
                return result
            try:
                self._run_steps(coverage, context, job_env, workspace, temporary, meta, log, result)
            finally:
                if config.cleanup:
                    self._cleanup(config.cleanup, context, workspace, temporary, meta, log)
            outputs = definition.get("outputs") or {}
            result.outputs = {name: to_string(interpolate(value, context))
                              for name, value in outputs.items()}
            return result
        except (LocalCIError, UnsupportedExpression) as error:
            result.conclusion = "failure"
            result.steps.append(StepResult("local CI", "failure", 0.0, str(error)))
            log.write(f"local CI error: {error}\n")
            return result
        finally:
            result.seconds = self.clock() - started
            log.close()

    @staticmethod
    def _job_status(name: str, needs: dict) -> bool:
        results = [need.get("result") for need in needs.values()]
        if name == "always":
            return True
        if name == "cancelled":
            return False
        if name == "failure":
            return any(value == "failure" for value in results)
        return all(value in ("success", "skipped") for value in results)

    def _env_values(self, raw, context) -> dict:
        if not raw:
            return {}
        if not isinstance(raw, dict):
            raise LocalCIError("an env block that is not a mapping")
        values = {}
        for name, value in raw.items():
            values[str(name)] = to_string(interpolate(value, {**context, "env": {
                **context.get("env", {}), **values}}))
        return values

    def _run_called(self, coverage: LegCoverage, context: dict, job_env: dict, slug: str):
        repository, revision, workflow = coverage.called_workflow
        self.inputs_used[f"{repository}:{workflow.path}"] = revision
        call = workflow.document.get("on", {})
        declared = (call.get("workflow_call") or {}).get("inputs") or {} if isinstance(call, dict) else {}
        given = interpolate(coverage.leg.definition.get("with") or {}, context)
        inputs = {name: (spec or {}).get("default") for name, spec in declared.items()}
        inputs.update(given)
        workflow_env = self._env_values(workflow.document.get("env"), {**context, "inputs": inputs})
        results: list[JobResult] = []
        finished: dict[str, dict] = {}
        pending = list(coverage.called)
        while pending:
            ready = [child for child in pending
                     if all(need in finished for need in child.leg.needs)]
            if not ready:
                raise LocalCIError(f"{workflow.path} has jobs whose needs never finish")
            for child in ready:
                pending.remove(child)
                needs = {need: finished[need] for need in child.leg.needs}
                child_result = self.run(child, needs, inputs=inputs, workflow_env=workflow_env,
                                        label=f"{slug}--{child.leg.leg}")
                results.append(child_result)
                finished[child.leg.job_id] = {"result": child_result.conclusion,
                                              "outputs": child_result.outputs}
        return results

    def _run_steps(self, coverage, context, job_env, workspace, temporary, meta, log, result):
        definition = coverage.leg.definition
        config = coverage.config
        defaults = {**((coverage.leg.workflow.document.get("defaults") or {}).get("run") or {}),
                    **((definition.get("defaults") or {}).get("run") or {})}
        deadline = self.clock() + 60 * float(interpolate(
            definition.get("timeout-minutes", DEFAULT_JOB_MINUTES), context))
        state = {"failed": False, "env": {}, "path": []}
        steps = definition.get("steps") or []
        for index, raw in enumerate(steps):
            key = step_key(raw, index)
            started = self.clock()
            if key in config.skip:
                result.steps.append(StepResult(key, "skipped", 0.0, f"skipped: {config.skip[key]}"))
                log.write(f"== {key}: skipped ({config.skip[key]})\n")
                continue
            step = dict(raw)
            if key in config.substitute:
                step.pop("uses", None)
                step["run"] = config.substitute[key]["run"]
            context["env"] = {**job_env, **state["env"]}
            step_env = self._env_values(step.get("env"), context)
            context["env"] = {**job_env, **state["env"], **step_env}
            status = lambda name: {"always": True, "cancelled": False,  # noqa: E731
                                   "failure": state["failed"], "success": not state["failed"]}[name]
            if not condition(step.get("if"), context, status):
                result.steps.append(StepResult(key, "skipped", 0.0, "condition false"))
                log.write(f"== {key}: condition false\n")
                self._record_step(context, step, "skipped", {})
                continue
            if self.clock() > deadline:
                raise LocalCIError(f"the job ran past its timeout before {key!r}")
            self.reporter(f"{self.repository} {coverage.leg.leg}: {key}")
            log.write(f"== {key}\n")
            outputs: dict = {}
            try:
                if "uses" in step:
                    outputs = self._action(step, context, workspace, temporary, state, log) or {}
                    outcome = "success"
                else:
                    outcome, outputs = self._shell(step, context, defaults, workspace, temporary,
                                                   meta, state, log, deadline)
            except LocalCIError as error:
                log.write(f"   {error}\n")
                outcome = "failure"
                result.steps.append(StepResult(key, "failure", self.clock() - started, str(error)))
                self._record_step(context, step, "failure", outputs)
                if not interpolate(step.get("continue-on-error", False), context):
                    state["failed"] = True
                    context["job"]["status"] = "failure"
                continue
            continue_on_error = bool(interpolate(step.get("continue-on-error", False), context))
            conclusion = "success" if outcome == "success" or continue_on_error else "failure"
            result.steps.append(StepResult(key, outcome if outcome == "success" else (
                "failure" if not continue_on_error else "failure (continued)"),
                self.clock() - started))
            self._record_step(context, step, outcome, outputs, conclusion)
            if conclusion == "failure":
                state["failed"] = True
                context["job"]["status"] = "failure"
        if state["failed"]:
            result.conclusion = "failure"

    def _cleanup(self, script, context, workspace, temporary, meta, log):
        """The registry's cleanup, always, after the steps: what an ephemeral runner discards."""
        log.write(f"== cleanup\n$ {script}\n")
        environment = self._process_environment(context, {"path": []}, workspace, temporary, meta)
        result = subprocess.run(["bash", "--noprofile", "--norc", "-c", script], cwd=str(workspace),
                                env=environment, text=True, capture_output=True, check=False)
        log.write((result.stdout or "") + (result.stderr or "") + f"-- exit {result.returncode}\n")

    @staticmethod
    def _record_step(context, step, outcome, outputs, conclusion=None):
        if step.get("id"):
            context["steps"][str(step["id"])] = {
                "outcome": outcome, "conclusion": conclusion or outcome, "outputs": outputs}

    def _process_environment(self, context, state, workspace, temporary, meta) -> dict:
        environment = dict(self.environment)
        if state["path"]:
            environment["PATH"] = os.pathsep.join([*reversed(state["path"]), environment["PATH"]])
        environment.update({
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_SHA": self.revision, "GITHUB_REF": "refs/heads/develop",
            "GITHUB_REF_NAME": "develop", "GITHUB_EVENT_NAME": "push",
            "GITHUB_REPOSITORY": f"metadatacenter/{self.repository}",
            "GITHUB_REPOSITORY_OWNER": "metadatacenter", "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": self.run_id, "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_PATH": context["github"]["event_path"],
            "GITHUB_STEP_SUMMARY": str(meta / "summary.md"),
            "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64", "RUNNER_TEMP": str(temporary),
            "TMPDIR": str(temporary),
        })
        maven_options = environment.get("MAVEN_OPTS", "")
        environment["MAVEN_OPTS"] = (f"{maven_options} " if maven_options else "") + \
            f"-Dmaven.repo.local={self.maven_repository}"
        environment.update(context["env"])
        return environment

    def _shell(self, step, context, defaults, workspace, temporary, meta, state, log, deadline):
        script = to_string(interpolate(step["run"], context))
        directory = step.get("working-directory") or defaults.get("working-directory") or ""
        directory = to_string(interpolate(directory, context)) if directory else ""
        cwd = (workspace / directory) if directory else workspace
        if not cwd.is_dir():
            raise LocalCIError(f"the working directory {directory} does not exist")
        shell = step.get("shell") or defaults.get("shell") or "bash"
        script_path = temporary / f"step-{uuid.uuid4().hex[:8]}.sh"
        script_path.write_text(script, encoding="utf-8")
        command = (["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script_path)]
                   if shell == "bash" else ["sh", "-e", str(script_path)])
        output_file = meta / f"output-{uuid.uuid4().hex[:8]}"
        env_file = meta / "env"
        path_file = meta / "path"
        for path in (output_file, env_file, path_file):
            path.write_text("")
        environment = self._process_environment(context, state, workspace, temporary, meta)
        environment.update({"GITHUB_OUTPUT": str(output_file), "GITHUB_ENV": str(env_file),
                            "GITHUB_PATH": str(path_file)})
        limit = step.get("timeout-minutes")
        remaining = deadline - self.clock()
        timeout = min(remaining, 60 * float(interpolate(limit, context))) if limit else remaining
        log.write(f"$ ({cwd.relative_to(workspace) if cwd != workspace else '.'}) {script.strip()}\n")
        process = self.command_runner(command, cwd=str(cwd), env=environment, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, errors="replace")
        timer = threading.Timer(max(timeout, 1), process.kill)
        timer.start()
        try:
            for line in process.stdout:
                log.write(line)
            code = process.wait()
        finally:
            timer.cancel()
        if remaining <= 0 or (code < 0 and self.clock() >= deadline):
            raise LocalCIError("the step ran past its timeout")
        state["env"].update(_parse_command_file(env_file))
        for line in path_file.read_text().splitlines():
            if line.strip():
                state["path"].append(line.strip())
        outputs = _parse_command_file(output_file)
        log.write(f"-- exit {code}\n")
        return ("success" if code == 0 else "failure"), outputs

    # -- actions ----------------------------------------------------------------------------

    def _action(self, step, context, workspace, temporary, state, log):
        action = str(step["uses"]).split("@", 1)[0].strip()
        if action.startswith("actions/cache"):
            log.write("   the cache is local; nothing to restore or save\n")
            return {"cache-hit": "false"}
        inputs = {name: to_string(value) for name, value in
                  (interpolate(step.get("with") or {}, context) or {}).items()}
        if action == "actions/checkout":
            return self._checkout(inputs, workspace, log)
        if action == "actions/setup-java":
            java_home = self.environment.get("JAVA_HOME")
            if not java_home:
                raise LocalCIError("no Java 17 is available to set up")
            state["path"].append(str(Path(java_home) / "bin"))
            return {}
        if action == "actions/setup-node":
            version = inputs.get("node-version", "").strip()
            if not version and inputs.get("node-version-file"):
                version = (workspace / inputs["node-version-file"]).read_text().strip()
            wanted = version if version.startswith("v") else f"v{version}"
            if wanted != REQUIRED_NODE:
                raise LocalCIError(f"the job asks for Node {version}; local CI runs Node {REQUIRED_NODE}")
            return {}
        if action == "actions/setup-python":
            version = inputs.get("python-version", "").strip()
            interpreter = python_interpreter(version)
            if not interpreter:
                raise LocalCIError(f"this machine has no Python {version}")
            venv = temporary / f"python-{version}"
            if not venv.exists():
                result = subprocess.run([interpreter, "-m", "venv", str(venv)], capture_output=True,
                                        text=True, check=False)
                if result.returncode:
                    raise LocalCIError(f"could not create a Python {version} environment: "
                                       f"{result.stderr.strip()}")
            state["path"].append(str(venv / "bin"))
            return {"python-version": version}
        if action == "actions/upload-artifact":
            return self._upload(inputs, workspace, log)
        if action == "actions/download-artifact":
            return self._download(inputs, workspace, log)
        raise LocalCIError(f"local CI does not implement {step['uses']}")

    def _checkout(self, inputs: dict, workspace: Path, log) -> dict:
        repository = inputs.get("repository") or f"metadatacenter/{self.repository}"
        name = repository.split("/", 1)[1] if "/" in repository else repository
        source = self.cedar_home / name
        if not (source / ".git").exists():
            raise LocalCIError(f"{name} is not checked out under {self.cedar_home}")
        ref = inputs.get("ref", "").strip()
        if name == self.repository and not ref:
            revision = self.revision
        else:
            with self.lock:
                if name not in self.fetched and name != self.repository:
                    git(source, "fetch", "-q", "origin", check=False)
                    self.fetched.add(name)
            revision = resolve_ref(source, ref or "develop")
        destination = workspace / inputs.get("path", "") if inputs.get("path") else workspace
        destination.mkdir(parents=True, exist_ok=True)
        if (destination / ".git").exists():
            raise LocalCIError(f"{destination} already holds a checkout")
        staging = Path(tempfile.mkdtemp(prefix="checkout-", dir=str(workspace.parent / "tmp")))
        try:
            git(staging.parent, "clone", "-q", "--no-checkout", "--local", str(source), str(staging / "clone"))
            shutil.move(str(staging / "clone" / ".git"), str(destination / ".git"))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        git(destination, "checkout", "-q", "--force", "--detach", revision)
        if name != self.repository or revision != self.revision:
            # The commit under test keeps its own entry; another commit of the same repository, such
            # as a performance comparison's base, is recorded beside it.
            key = name if name != self.repository else f"{name}@{revision}"
            with self.lock:
                self.inputs_used[key] = revision
        log.write(f"   {name} at {revision}\n")
        return {"commit": revision, "ref": ref}

    def _artifact_files(self, patterns: list[str], workspace: Path) -> list[Path]:
        included: set[Path] = set()
        excluded: set[Path] = set()
        for pattern in patterns:
            negate = pattern.startswith("!")
            text = pattern[1:] if negate else pattern
            base = Path(text) if Path(text).is_absolute() else workspace / text
            matches = [Path(item) for item in glob.glob(str(base), recursive=True)]
            files: set[Path] = set()
            for match in matches:
                if match.is_dir():
                    files.update(path for path in match.rglob("*") if path.is_file())
                elif match.is_file():
                    files.add(match)
            (excluded if negate else included).update(files)
        return sorted(included - excluded)

    @staticmethod
    def _artifact_root(patterns: list[str], workspace: Path) -> Path:
        """Where upload-artifact roots an artifact: the common ancestor of the paths it was given.

        A directory given as a path is the root of its own contents; a file or a glob roots at the
        directory above it.
        """
        roots = []
        for pattern in patterns:
            if pattern.startswith("!"):
                continue
            base = Path(pattern) if Path(pattern).is_absolute() else workspace / pattern
            literal = []
            for part in base.parts:
                if any(character in part for character in "*?["):
                    break
                literal.append(part)
            prefix = Path(*literal)
            roots.append(prefix if prefix.is_dir() and len(literal) == len(base.parts) else (
                prefix if any(character in str(base) for character in "*?[") else prefix.parent))
        return Path(os.path.commonpath([str(root) for root in roots])) if roots else workspace

    def _upload(self, inputs: dict, workspace: Path, log) -> dict:
        name = inputs.get("name") or "artifact"
        patterns = [line.strip() for line in inputs.get("path", "").splitlines() if line.strip()]
        files = self._artifact_files(patterns, workspace)
        if not files:
            behaviour = inputs.get("if-no-files-found", "warn")
            if behaviour == "error":
                raise LocalCIError(f"no files matched the artifact {name}")
            log.write(f"   no files matched the artifact {name}\n")
            return {}
        root = self._artifact_root(patterns, workspace)
        target = self.artifacts / name
        if target.exists():
            shutil.rmtree(target)
        for path in files:
            destination = target / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        log.write(f"   kept {len(files)} file(s) as the artifact {name}\n")
        return {"artifact-id": name}

    def _download(self, inputs: dict, workspace: Path, log) -> dict:
        name = inputs.get("name")
        path = inputs.get("path")
        destination = (Path(path) if path and Path(path).is_absolute() else workspace / path) \
            if path else workspace
        names = [name] if name else sorted(item.name for item in self.artifacts.iterdir()) \
            if self.artifacts.exists() else []
        for artifact in names:
            source = self.artifacts / artifact
            if not source.is_dir():
                raise LocalCIError(f"no earlier job kept the artifact {artifact}")
            target = destination if name else destination / artifact
            shutil.copytree(source, target, dirs_exist_ok=True)
            log.write(f"   restored the artifact {artifact} into {target}\n")
        return {"download-path": str(destination)}


def hash_files(workspace: Path):
    def function(*patterns):
        digest = hashlib.sha256()
        matched = False
        for pattern in patterns:
            for path in sorted(glob.glob(str(workspace / str(pattern)), recursive=True)):
                if Path(path).is_file():
                    digest.update(hashlib.sha256(Path(path).read_bytes()).digest())
                    matched = True
        return digest.hexdigest() if matched else ""
    return function


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

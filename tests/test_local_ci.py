"""Local CI: running a repository's own workflows here, and the evidence a gate reads."""
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from org.metadatacenter.local_ci import evidence
from org.metadatacenter.local_ci.coverage import repository_coverage
from org.metadatacenter.local_ci.errors import LocalCIError
from org.metadatacenter.local_ci.expressions import (
    UnsupportedExpression, condition, evaluate, interpolate)
from org.metadatacenter.local_ci.registry import Registry
from org.metadatacenter.local_ci.run import run
from org.metadatacenter.local_ci.workflows import load_document, matrix_legs, triggered_by_push


# Expressions ----------------------------------------------------------------------------------


CONTEXT = {
    "github": {"event_name": "push", "ref": "refs/heads/develop",
               "event": {"before": "abc", "pull_request": None}},
    "matrix": {"os": "macos-latest", "python": "3.12"},
    "env": {"NAME": "value"},
    "steps": {"build": {"outputs": {"version": "1.2.3"}}},
    "secrets": {"TOKEN": "s3cret"},
}


@pytest.mark.parametrize("expression, expected", [
    ("github.event_name == 'push' && github.ref == 'refs/heads/develop'", True),
    ("github.event_name == 'PUSH'", True),
    ("github.event.pull_request.base.sha || github.event.before", "abc"),
    ("matrix.os != 'ubuntu-latest'", True),
    ("steps.build.outputs.version", "1.2.3"),
    ("format('{0}-{1}', matrix.os, matrix.python)", "macos-latest-3.12"),
    ("contains(fromJSON('[\"a\",\"b\"]'), 'B')", True),
    ("startsWith(github.ref, 'refs/heads/')", True),
    ("!(1 < 2)", False),
    ("env.MISSING", None),
])
def test_expressions_evaluate_as_github_does(expression, expected):
    assert evaluate(expression, CONTEXT) == expected


def test_an_unknown_context_or_function_is_refused_rather_than_guessed():
    with pytest.raises(UnsupportedExpression):
        evaluate("vars.SOMETHING", CONTEXT)
    with pytest.raises(UnsupportedExpression):
        evaluate("hashFiles('x')", CONTEXT)


def test_conditions_require_success_unless_they_name_a_status():
    failed = lambda name: {"success": False, "failure": True, "always": True, "cancelled": False}[name]  # noqa: E731
    assert condition("github.event_name == 'push'", CONTEXT, failed) is False
    assert condition("${{ failure() }}", CONTEXT, failed) is True
    assert condition("always()", CONTEXT, failed) is True
    assert condition(None, CONTEXT, failed) is False


def test_interpolation_substitutes_inside_text_and_keeps_whole_values():
    assert interpolate("v=${{ steps.build.outputs.version }}!", CONTEXT) == "v=1.2.3!"
    assert interpolate({"a": ["${{ matrix.os }}"]}, CONTEXT) == {"a": ["macos-latest"]}


# Workflows ------------------------------------------------------------------------------------


def test_yaml_s_on_key_is_read_as_the_trigger():
    document = load_document("on:\n  push:\n    branches: [develop]\njobs: {}\n", "x")
    assert "on" in document and triggered_by_push(document, "develop", ["a"])


@pytest.mark.parametrize("trigger, changed, expected", [
    ({"push": {"branches": ["develop"]}}, ["src/a.py"], True),
    ({"push": {"branches": ["main"]}}, ["src/a.py"], False),
    ({"push": {"branches": ["develop"], "paths": ["pom.xml", "**/pom.xml"]}}, ["src/a.py"], False),
    ({"push": {"branches": ["develop"], "paths": ["pom.xml", "**/pom.xml"]}}, ["a/pom.xml"], True),
    ({"push": {"paths-ignore": ["docs/**"]}}, ["docs/a.md"], False),
    ({"pull_request": None}, ["x"], False),
    ({"push": {"tags": ["v*"]}}, ["x"], False),
])
def test_push_triggers_follow_branch_and_path_filters(trigger, changed, expected):
    assert triggered_by_push({"on": trigger}, "develop", changed) is expected


def test_matrices_expand_as_github_does():
    legs = matrix_legs({"matrix": {"os": ["ubuntu", "macos"], "python": ["3.10", "3.12"],
                                   "exclude": [{"os": "macos", "python": "3.10"}],
                                   "include": [{"os": "ubuntu", "python": "3.10", "extra": True},
                                               {"os": "windows", "python": "3.12"}]}})
    assert {"os": "ubuntu", "python": "3.10", "extra": True} in legs
    assert {"os": "macos", "python": "3.10"} not in legs
    assert {"os": "windows", "python": "3.12"} in legs
    assert len(legs) == 4


# A repository to run ------------------------------------------------------------------------


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


WORKFLOW = textwrap.dedent("""\
    name: CI
    on:
      push:
        branches: [develop]
    env:
      GREETING: hello
    jobs:
      build:
        runs-on: ubuntu-latest
        outputs:
          answer: ${{ steps.compute.outputs.answer }}
        env:
          TARGET: world
        steps:
          - uses: actions/checkout@v4
          - name: Install system packages
            run: sudo apt-get install -y jq
          - name: Compute
            id: compute
            run: |
              echo "answer=42" >> "$GITHUB_OUTPUT"
              echo "LATER=from-env-file" >> "$GITHUB_ENV"
              test "$GREETING $TARGET" = "hello world"
              test -z "${CEDAR_SHOULD_NOT_LEAK:-}"
          - name: Use the environment file
            run: test "$LATER" = "from-env-file"
          - name: Make an artifact
            run: mkdir -p out/deep && echo built > out/deep/file.txt
          - uses: actions/upload-artifact@v4
            with:
              name: result
              path: out/
          - name: Publish
            if: github.event_name == 'push' && github.ref == 'refs/heads/develop'
            run: ./mvnw deploy -DskipTests
      check:
        needs: build
        runs-on: ubuntu-latest
        steps:
          - uses: actions/download-artifact@v4
            with:
              name: result
              path: restored
          - name: Read the artifact and the output
            run: |
              test "$(cat restored/deep/file.txt)" = built
              test "${{ needs.build.outputs.answer }}" = 42
          - name: Only on failure
            if: failure()
            run: exit 1
    """)

REGISTRY = {
    "schemaVersion": 1,
    "statusContext": "cedar/local-ci",
    "profiles": {"publishing": {"skip": {"Publish": "publication, not verification"}}},
    "repositories": {"cedar-sample": {"jobs": {
        "ci.yml:build": {"profile": "publishing",
                         "skip": {"Install system packages": "this machine has jq"},
                         "cleanup": "touch \"$RUNNER_TEMP/../cleaned\""},
        "ci.yml:check": {}}}},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repository = home / "cedar-sample"
    (repository / ".github" / "workflows").mkdir(parents=True)
    (repository / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
    (repository / "README.md").write_text("sample\n")
    git(repository, "init", "-q", "-b", "develop")
    git(repository, "config", "user.email", "ci@example.org")
    git(repository, "config", "user.name", "CI")
    git(repository, "add", "-A")
    git(repository, "commit", "-q", "-m", "Initial")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(repository), str(origin))
    git(repository, "remote", "add", "origin", str(origin))
    git(repository, "fetch", "-q", "origin")
    ops = home / "cedar-development" / "ops"
    ops.mkdir(parents=True)
    (ops / "local-ci.json").write_text(json.dumps(REGISTRY))
    monkeypatch.setenv("CEDAR_LOCAL_CI_DIR", str(tmp_path / "local-ci"))
    return home


def _environment(tmp_path):
    import os
    return {**os.environ, "CEDAR_LOCAL_CI_DIR": str(tmp_path / "local-ci"),
            "CEDAR_SHOULD_NOT_LEAK": "from the developer shell"}


def test_a_covered_repository_runs_its_own_steps_and_records_a_pass(home, tmp_path):
    lines = []
    [outcome] = run(home, ["cedar-sample"], post=False, reporter=lines.append,
                    source_environment=_environment(tmp_path))

    assert outcome.state == "passed", (outcome.detail, Path(outcome.record_path).read_text()
                                       if outcome.record_path else "")
    record = json.loads(Path(outcome.record_path).read_text())
    revision = git(home / "cedar-sample", "rev-parse", "HEAD")
    assert record["revision"] == revision and record["conclusion"] == "success" and record["covered"]
    assert record["inputs"]["cedar-sample"] == revision
    build = next(job for job in record["jobs"] if job["leg"] == "ci.yml:build")
    steps = {step["name"]: step for step in build["steps"]}
    assert steps["Publish"]["conclusion"] == "skipped"
    assert steps["Install system packages"]["conclusion"] == "skipped"
    assert steps["Compute"]["conclusion"] == "success"
    assert build["logSha256"]
    check = next(job for job in record["jobs"] if job["leg"] == "ci.yml:check")
    assert {step["name"]: step["conclusion"] for step in check["steps"]}["Only on failure"] == "skipped"
    assert list((Path(record["runDirectory"]) / "jobs").glob("*/cleaned"))
    answer = evidence.verdict(home, "cedar-sample", revision,
                              registry=Registry(REGISTRY), read_status=False)
    assert answer.green, answer.detail


def test_a_failing_step_fails_the_job_and_the_record_is_red(home, tmp_path):
    workflow = home / "cedar-sample" / ".github" / "workflows" / "ci.yml"
    workflow.write_text(WORKFLOW.replace('test "$LATER" = "from-env-file"', "exit 3"))
    git(home / "cedar-sample", "commit", "-qam", "Break it")
    [outcome] = run(home, ["cedar-sample"], post=False, reporter=lambda line: None,
                    revisions={"cedar-sample": "HEAD"}, source_environment=_environment(tmp_path))
    assert outcome.state == "failed"
    revision = git(home / "cedar-sample", "rev-parse", "HEAD")
    answer = evidence.verdict(home, "cedar-sample", revision, registry=Registry(REGISTRY),
                              read_status=False)
    assert answer.state == "red" and "ci.yml:build" in answer.detail


def test_an_unregistered_publishing_or_package_installing_step_leaves_the_job_to_github(home):
    registry = Registry({**REGISTRY, "repositories": {"cedar-sample": {"jobs": {
        "ci.yml:build": {}, "ci.yml:check": {}}}}})
    revision = git(home / "cedar-sample", "rev-parse", "HEAD")
    coverage = repository_coverage(home, "cedar-sample", revision, registry)
    reasons = "\n".join(coverage.reasons())
    assert not coverage.covered
    assert "'Publish' publishes" in reasons
    assert "'Install system packages' installs system packages" in reasons


def test_a_workflow_change_the_registry_has_not_reviewed_is_drift(home):
    workflow = home / "cedar-sample" / ".github" / "workflows" / "ci.yml"
    workflow.write_text(WORKFLOW.replace("      - name: Publish\n", "      - name: Release it\n")
                        + "  lint:\n    runs-on: ubuntu-latest\n    container: node:24\n"
                        "    steps:\n      - run: npm run lint\n        env:\n"
                        "          TOKEN: ${{ secrets.NPM_TOKEN }}\n")
    git(home / "cedar-sample", "commit", "-qam", "Change the workflow")
    revision = git(home / "cedar-sample", "rev-parse", "HEAD")
    coverage = repository_coverage(home, "cedar-sample", revision, Registry(REGISTRY))
    reasons = "\n".join(coverage.reasons())
    assert "names a step 'Publish' the job no longer has" in reasons
    assert "'Release it' publishes" in reasons
    assert "registry names no job ci.yml:lint" in reasons
    registry = Registry({**REGISTRY, "repositories": {"cedar-sample": {"jobs": {
        **REGISTRY["repositories"]["cedar-sample"]["jobs"], "ci.yml:lint": {}}}}})
    reasons = "\n".join(repository_coverage(home, "cedar-sample", revision, registry).reasons())
    assert "runs in a container" in reasons and "secret NPM_TOKEN" in reasons


def test_the_registry_must_give_reasons():
    registry = Registry({"schemaVersion": 1, "repositories": {"r": {"jobs": {
        "ci.yml:x": {"skip": {"Publish": ""}}}}}})
    with pytest.raises(LocalCIError, match="without a reason"):
        registry.job("r", "ci.yml:x")


def test_an_uncovered_repository_is_not_run_and_says_why(home, tmp_path):
    registry = home / "cedar-development" / "ops" / "local-ci.json"
    registry.write_text(json.dumps({**REGISTRY, "repositories": {"cedar-sample": {"jobs": {
        "ci.yml:build": {"uncovered": "needs Linux"}, "ci.yml:check": {}}}}}))
    [outcome] = run(home, ["cedar-sample"], post=False, reporter=lambda line: None,
                    source_environment=_environment(tmp_path))
    assert outcome.state == "uncovered"
    assert any("needs Linux" in reason for reason in outcome.reasons)


# Evidence and status --------------------------------------------------------------------------


class FakeGh:
    def __init__(self, statuses=None):
        self.calls = []
        self.statuses = statuses or []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if "--method" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({"url": "https://api/status/1"}), "")
        return subprocess.CompletedProcess(command, 0, json.dumps(self.statuses), "")


def test_a_run_posts_its_verdict_as_a_commit_status():
    gh = FakeGh()
    record = {"repository": "cedar-sample", "revision": "a" * 40, "conclusion": "success",
              "host": "workstation", "registrySha256": "b" * 64,
              "jobs": [{"seconds": 120}, {"seconds": 60}]}
    posted = evidence.post_status(record, "cedar/local-ci", runner=gh)
    [command] = gh.calls
    assert command[:4] == ["gh", "api", "--method", "POST"]
    assert command[4] == f"repos/metadatacenter/cedar-sample/statuses/{'a' * 40}"
    assert "state=success" in command and "context=cedar/local-ci" in command
    description = next(item for item in command if item.startswith("description="))
    assert "2 job(s) passed on workstation in 3 min; registry bbbbbbbb" in description
    assert posted["state"] == "success"


def test_another_machine_s_status_answers_when_this_machine_has_no_record(tmp_path):
    gh = FakeGh([{"context": "cedar/local-ci", "state": "success", "description": "passed",
                  "updated_at": "2026-11-09T10:00:00Z"},
                 {"context": "other", "state": "failure", "updated_at": "2026-11-09T11:00:00Z"}])
    answer = evidence.verdict(tmp_path, "cedar-sample", "c" * 40, runner=gh,
                              environment={"CEDAR_LOCAL_CI_DIR": str(tmp_path / "none")})
    assert answer.green and answer.source == "status"


def test_a_record_made_under_another_registry_entry_answers_for_nothing(tmp_path):
    environment = {"CEDAR_LOCAL_CI_DIR": str(tmp_path)}
    record = {"schemaVersion": 1, "repository": "r", "revision": "d" * 40, "tree": "e" * 40,
              "conclusion": "success", "covered": True, "registrySha256": "old", "jobs": []}
    evidence.write_record(record, environment)
    answer = evidence.ledger_verdict(tmp_path, "r", "d" * 40, "new", environment)
    assert answer.state == "none" and "earlier registry" in answer.detail


# Gates --------------------------------------------------------------------------------------


class Jobs:
    def __init__(self, payload, code=0):
        self.payload, self.code, self.calls = payload, code, []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, self.code, json.dumps(self.payload), "")


@pytest.mark.parametrize("run_record, payload, expected", [
    ({"status": "completed", "conclusion": "startup_failure", "id": 1}, None, True),
    ({"status": "completed", "conclusion": "failure", "id": 1},
     {"jobs": [{"name": "WebKit", "conclusion": "skipped"}]}, True),
    ({"status": "completed", "conclusion": "failure", "id": 1}, {"jobs": []}, True),
    ({"status": "completed", "conclusion": "failure", "id": 1},
     {"jobs": [{"name": "test", "conclusion": "failure"}]}, False),
    ({"status": "completed", "conclusion": "failure", "id": 1}, {"workflow_runs": []}, False),
    ({"status": "in_progress", "conclusion": None, "id": 1}, None, False),
])
def test_github_s_own_failures_are_told_apart_from_the_code_s(run_record, payload, expected):
    from org.metadatacenter.local_ci.gates import github_failed_to_start
    assert github_failed_to_start("cedar-sample", run_record, runner=Jobs(payload)) is expected


def test_a_gate_reads_local_ci_s_answer_from_the_home_it_is_given(home, tmp_path):
    from org.metadatacenter.local_ci.gates import local_answer
    environment = _environment(tmp_path)
    [outcome] = run(home, ["cedar-sample"], post=False, reporter=lambda line: None,
                    source_environment=environment)
    revision = outcome.revision
    answer = local_answer(home, "cedar-sample", revision, environment=environment, read_status=False)
    assert answer.green, answer.detail
    assert local_answer(home, "cedar-other", revision, environment=environment).state == "none"
    assert local_answer(None, "cedar-sample", revision).state == "none"


def test_the_release_preflight_accepts_local_ci_without_asking_github(home, tmp_path):
    from org.metadatacenter.release_support.preflight import ReleasePreflight
    environment = {**_environment(tmp_path), "CEDAR_HOME": str(home)}
    [outcome] = run(home, ["cedar-sample"], post=False, reporter=lambda line: None,
                    source_environment=environment)
    calls = []

    def commands(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["git", "-C"]:
            return subprocess.CompletedProcess(args, 0, ".github/workflows/ci.yml\n", "")
        return subprocess.CompletedProcess(args, 0, json.dumps({"workflow_runs": []}), "")

    preflight = ReleasePreflight.__new__(ReleasePreflight)
    preflight.manifest = {"releaseRepositories": ["cedar-sample"],
                          "sourceRepositories": {"cedar-sample": outcome.revision}}
    preflight.environment = environment
    preflight.command_runner = commands
    preflight.ci_sleeper = lambda seconds: None
    preflight.ci_delays = ()
    preflight.accepted_red_develop = {}
    preflight._source_paths = lambda repository: [".github/workflows/ci.yml"]
    import shutil as _shutil
    original = _shutil.which
    _shutil.which = lambda name, path=None: "/usr/bin/gh" if name == "gh" else original(name, path=path)
    try:
        findings = preflight.check_develop_is_green()
    finally:
        _shutil.which = original
    assert findings == []
    assert not any(call[:2] == ["gh", "api"] and "actions/runs" in " ".join(call) for call in calls)

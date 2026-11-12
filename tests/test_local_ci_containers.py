"""Local CI's Linux legs: which legs run in a container, and how a step reaches one.

Docker is stood in for by a runner that executes what `docker exec` would run on this machine, so
the environment file, the loader and the setup actions are exercised for real without a daemon.
"""
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from org.metadatacenter.local_ci import containers
from org.metadatacenter.local_ci.containers import Container
from org.metadatacenter.local_ci.coverage import repository_coverage, runs_in_linux
from org.metadatacenter.local_ci.errors import LocalCIError
from org.metadatacenter.local_ci.executor import JobRunner
from org.metadatacenter.local_ci.registry import Registry


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def repository(home: Path, workflow: str) -> str:
    root = home / "cedar-sample"
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(workflow)
    (root / "README.md").write_text("sample\n")
    if not (root / ".git").exists():
        git(root, "init", "-q", "-b", "develop")
        git(root, "config", "user.email", "ci@example.org")
        git(root, "config", "user.name", "CI")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Workflow")
    return git(root, "rev-parse", "HEAD")


def registry(jobs: dict) -> Registry:
    return Registry({"schemaVersion": 1, "repositories": {"cedar-sample": {"jobs": jobs}}})


MATRIX = textwrap.dedent("""\
    name: CI
    on:
      push:
        branches: [develop]
    jobs:
      test:
        runs-on: ${{ matrix.os }}
        strategy:
          matrix:
            os: [ubuntu-latest, macos-latest]
            python: ["3.10", "3.12"]
        steps:
          - uses: actions/setup-python@v6
            with:
              python-version: ${{ matrix.python }}
          - name: Mount a noexec temporary directory
            if: matrix.os == 'ubuntu-latest' && matrix.python == '3.10'
            run: sudo mount -t tmpfs tmpfs "$RUNNER_TEMP/noexec"
          - run: python -m pytest
    """)


def legs(home, revision, jobs):
    coverage = repository_coverage(home, "cedar-sample", revision, registry(jobs))
    return coverage, {leg.leg.leg: leg for leg in coverage.legs}


def test_a_linux_entry_runs_the_ubuntu_legs_in_the_runner_image_and_the_macos_legs_here(tmp_path, monkeypatch):
    revision = repository(tmp_path, MATRIX)
    monkeypatch.setattr("org.metadatacenter.local_ci.coverage.python_interpreter", lambda version: "/usr/bin/python3")
    coverage, by_leg = legs(tmp_path, revision, {"ci.yml:test": {"linux": "the Ubuntu legs test Linux behaviour"}})
    assert coverage.covered, coverage.reasons()
    ubuntu = by_leg["ci.yml:test [os=ubuntu-latest, python=3.10]"]
    macos = by_leg["ci.yml:test [os=macos-latest, python=3.10]"]
    assert runs_in_linux(ubuntu.leg, ubuntu.config) and not runs_in_linux(macos.leg, macos.config)


def test_without_a_linux_entry_an_ubuntu_leg_runs_here_and_its_sudo_step_leaves_it_to_github(tmp_path, monkeypatch):
    revision = repository(tmp_path, MATRIX)
    monkeypatch.setattr("org.metadatacenter.local_ci.coverage.python_interpreter", lambda version: "/usr/bin/python3")
    coverage, by_leg = legs(tmp_path, revision, {"ci.yml:test": {}})
    assert not coverage.covered
    reasons = "\n".join(coverage.reasons())
    assert "[os=ubuntu-latest, python=3.10]: step 'Mount a noexec temporary directory' installs" in reasons
    # The step's condition is false on every other leg, so it never decides their coverage.
    assert by_leg["ci.yml:test [os=macos-latest, python=3.10]"].covered
    assert by_leg["ci.yml:test [os=ubuntu-latest, python=3.12]"].covered


def test_a_linux_leg_asks_the_runner_image_for_its_python_and_a_macos_leg_asks_this_machine(tmp_path, monkeypatch):
    revision = repository(tmp_path, MATRIX.replace('python: ["3.10", "3.12"]', 'python: ["3.11"]'))
    monkeypatch.setattr("org.metadatacenter.local_ci.coverage.python_interpreter", lambda version: None)
    coverage, _ = legs(tmp_path, revision, {"ci.yml:test": {"linux": "Linux behaviour"}})
    reasons = "\n".join(coverage.reasons())
    assert "[os=ubuntu-latest, python=3.11]: step '#1' asks for Python 3.11, which the runner image" in reasons
    assert "[os=macos-latest, python=3.11]: step '#1' asks for Python 3.11, which this machine" in reasons
    assert "uv python install 3.11" in reasons


CONTAINER = textwrap.dedent("""\
    name: CI
    on:
      push:
        branches: [develop]
    jobs:
      test:
        runs-on: ubuntu-latest
        outputs:
          version: ${{ steps.version.outputs.version }}
        steps:
          - id: version
            run: echo "version=1.64.1" >> "$GITHUB_OUTPUT"
      browser:
        needs: test
        runs-on: ubuntu-latest
        container:
          image: mcr.microsoft.com/playwright:v${{ needs.test.outputs.version }}-noble
        steps:
          - run: npm run test:browser
    """)


def test_a_job_container_runs_only_once_the_registry_says_why_arm64_answers(tmp_path):
    revision = repository(tmp_path, CONTAINER)
    coverage, _ = legs(tmp_path, revision, {"ci.yml:test": {}, "ci.yml:browser": {}})
    assert "the job runs in a container; the registry's linux entry" in "\n".join(coverage.reasons())
    coverage, by_leg = legs(tmp_path, revision, {"ci.yml:test": {}, "ci.yml:browser": {
        "linux": "the conformance tests read JavaScript's regex engine, whatever the processor"}})
    assert coverage.covered, coverage.reasons()
    browser = by_leg["ci.yml:browser"]
    assert runs_in_linux(browser.leg, browser.config)


def test_a_job_container_s_options_and_an_unknown_ubuntu_release_are_left_to_github(tmp_path):
    workflow = CONTAINER.replace("      image: mcr", "      options: --user 1001\n      image: mcr")
    workflow = workflow.replace("  test:\n    runs-on: ubuntu-latest", "  test:\n    runs-on: ubuntu-22.04")
    assert "options:" in workflow and "ubuntu-22.04" in workflow
    revision = repository(tmp_path, workflow)
    coverage, _ = legs(tmp_path, revision, {"ci.yml:test": {"linux": "x"}, "ci.yml:browser": {"linux": "x"}})
    reasons = "\n".join(coverage.reasons())
    assert "the job container sets options, which local CI does not" in reasons
    assert "local CI has no runner image for ubuntu-22.04" in reasons


def test_a_linux_entry_must_give_a_reason():
    with pytest.raises(LocalCIError, match="without saying why"):
        registry({"ci.yml:x": {"linux": " "}}).job("cedar-sample", "ci.yml:x")


# The loader and a step in a container ---------------------------------------------------------


def test_the_loader_exports_every_value_whole_skips_bad_names_and_deletes_its_file(tmp_path):
    variables = tmp_path / "variables"
    containers.write_environment(variables, {"MULTI": "a\nb=c", "EMPTY": "", "bad-name": "x",
                                             "QUOTED": "it's \"quoted\" $HOME"})
    assert stat.S_IMODE(variables.stat().st_mode) == 0o600
    result = subprocess.run(["bash", "--noprofile", "--norc", "-c", containers.LOADER, "local-ci",
                             str(variables), "bash", "-c",
                             'printf "%s|%s|%s|%s" "$MULTI" "${EMPTY-unset}" "$QUOTED" "$(env | grep -c bad-name)"'],
                            text=True, capture_output=True, env={"PATH": "/usr/bin:/bin"})
    assert result.stdout == "a\nb=c||it's \"quoted\" $HOME|0", result.stderr
    assert not variables.exists()


class LocalDocker:
    """Docker stood in for here. A container's paths are directories on this machine, so what
    `docker exec` would run runs here, and `docker cp` copies between directories."""

    def __init__(self):
        self.commands = []

    @staticmethod
    def exec_argv(command):
        arguments = command[2:]
        cwd = None
        while arguments[:1] in (["--workdir"], ["--user"]):
            if arguments[0] == "--workdir":
                cwd = arguments[1]
            arguments = arguments[2:]
        return arguments[1:], cwd

    def popen(self, command, **kwargs):
        self.commands.append(list(command))
        assert command[:2] == ["docker", "exec"], command
        argv, cwd = self.exec_argv(command)
        return subprocess.Popen(argv, cwd=cwd, env={"PATH": "/usr/bin:/bin"},
                                stdout=kwargs.get("stdout"), stderr=kwargs.get("stderr"),
                                text=True, errors="replace")

    def run(self, command, **kwargs):
        self.commands.append(list(command))
        if command[:2] == ["docker", "exec"]:
            argv, cwd = self.exec_argv(command)
            return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
        if command[:2] == ["docker", "cp"]:
            source, destination = (part.split(":", 1)[-1] for part in command[2:4])
            if source.endswith("/."):
                shutil.copytree(source[:-2], destination, dirs_exist_ok=True)
            else:
                shutil.copytree(source, destination) if Path(source).is_dir() else shutil.copy2(source, destination)
        return subprocess.CompletedProcess(command, 0, "", "")


@pytest.fixture
def local_container(tmp_path, monkeypatch):
    """A leg's container as directories under tmp_path, its file commands those of the named leg."""
    docker = LocalDocker()
    started = []
    leg = {"slug": None}
    root = tmp_path / "container"
    tool_cache = root / "toolcache"
    for tool in ("node/24.22.1/bin/node", "java/17/bin/java"):
        (tool_cache / tool).parent.mkdir(parents=True)
        (tool_cache / tool).write_text("#!/bin/sh\n")

    def places(kind, repository):
        return containers.Places(str(root / leg["slug"] / "work" / repository / repository),
                                 str(root / leg["slug"] / "work" / "_temp"),
                                 str(tmp_path / "run" / "jobs" / leg["slug"] / "meta"),
                                 str(tool_cache), "/home/runner")

    def start(**kwargs):
        started.append(kwargs)
        where = places(kwargs["kind"], kwargs["repository"])
        assert str(kwargs["commands"]) == where.commands
        Path(where.temporary).mkdir(parents=True)
        return Container(kwargs["name"], kwargs["image"], kwargs["kind"], where, "/usr/bin:/bin",
                         {"PATH": "/usr/bin:/bin"}, "sha256:abc", owner=f"{os.getuid()}:{os.getgid()}",
                         runner=docker.run)

    monkeypatch.setattr(containers, "places", places)
    monkeypatch.setattr(containers, "ensure_runner_image", lambda release, environment, log: f"runner:{release}")
    monkeypatch.setattr(containers, "start", start)
    return SimpleNamespace(docker=docker, started=started, leg=leg, run=tmp_path / "run")


STEPS = textwrap.dedent("""\
    name: CI
    on:
      push:
        branches: [develop]
    env:
      MULTI: |-
        first
        second
    jobs:
      test:
        runs-on: ubuntu-24.04-arm
        steps:
          - uses: actions/checkout@v4
          - uses: actions/setup-node@v4
            with:
              node-version: 24.22.1
          - uses: actions/setup-java@v6
            with:
              java-version: "17"
          - name: Read what the container hands the step
            env:
              SECRET: ${{ secrets.BMIR_NEXUS_PASSWORD }}
            run: |
              test "$RUNNER_OS" = Linux
              test "$HOME" = /home/runner
              test "$PWD" = "$GITHUB_WORKSPACE"
              test -z "${TMPDIR:-}"
              test -z "${CEDAR_SHOULD_NOT_LEAK:-}"
              test "$SECRET" = hunter2
              test "$MULTI" = "$(printf 'first\\nsecond')"
              test "$JAVA_HOME" = "$RUNNER_TOOL_CACHE/java/17"
              case ":$PATH:" in *":$RUNNER_TOOL_CACHE/node/24.22.1/bin:"*) ;; *) exit 9 ;; esac
              test -f README.md
              mkdir -p out/deep && echo built > out/deep/file.txt
          - uses: actions/upload-artifact@v4
            with:
              name: result
              path: out/
      check:
        needs: test
        runs-on: ubuntu-24.04-arm
        steps:
          - uses: actions/download-artifact@v4
            with:
              name: result
              path: restored
          - run: test "$(cat restored/deep/file.txt)" = built
    """)


def test_a_step_in_a_container_gets_its_environment_from_a_file_and_never_on_a_command_line(tmp_path, local_container):
    home = tmp_path / "home"
    revision = repository(home, STEPS)
    coverage = repository_coverage(home, "cedar-sample", revision,
                                   registry({"ci.yml:test": {"linux": "arm64 Ubuntu, as on GitHub"},
                                             "ci.yml:check": {"linux": "arm64 Ubuntu, as on GitHub"}}))
    assert coverage.covered, coverage.reasons()
    runner = JobRunner(cedar_home=home, repository="cedar-sample", revision=revision, parent=None,
                       run_directory=local_container.run,
                       environment={"PATH": "/usr/bin:/bin", "CEDAR_SHOULD_NOT_LEAK": "shell"},
                       secrets={"BMIR_NEXUS_PASSWORD": "hunter2"}, maven_repository=tmp_path / "m2",
                       command_runner=local_container.docker.popen, run_id="test")
    test, check = coverage.legs

    local_container.leg["slug"] = "ci.yml-test"
    result = runner.run(test, {})

    assert result.conclusion == "success", Path(result.log).read_text()
    assert result.container["kind"] == "runner" and result.container["platform"] == "linux/arm64"
    assert result.container["image"] == "runner:24.04"
    started = local_container.started
    assert started[0]["image"] == "runner:24.04" and started[0]["kind"] == "runner"
    commands = local_container.docker.commands
    assert not any("hunter2" in argument for command in commands for argument in command)
    assert not list(local_container.run.rglob("variables-*"))
    assert ["docker", "rm", "--force", "--volumes", started[0]["name"]] in commands
    assert (runner.artifacts / "result" / "deep" / "file.txt").read_text() == "built\n"

    local_container.leg["slug"] = "ci.yml-check"
    checked = runner.run(check, {"test": {"result": "success", "outputs": {}}})
    assert checked.conclusion == "success", Path(checked.log).read_text()


def test_a_job_container_s_image_is_read_from_the_needs_it_names(tmp_path, local_container):
    home = tmp_path / "home"
    revision = repository(home, CONTAINER)
    coverage = repository_coverage(home, "cedar-sample", revision, registry(
        {"ci.yml:test": {}, "ci.yml:browser": {"linux": "arm64 runs the same JavaScript"}}))
    browser = next(leg for leg in coverage.legs if leg.leg.job_id == "browser")
    runner = JobRunner(cedar_home=home, repository="cedar-sample", revision=revision, parent=None,
                       run_directory=local_container.run, environment={"PATH": "/usr/bin:/bin"},
                       secrets={}, maven_repository=tmp_path / "m2",
                       command_runner=local_container.docker.popen, run_id="test")
    local_container.leg["slug"] = "ci.yml-browser"
    runner.run(browser, {"test": {"result": "success", "outputs": {"version": "1.64.1"}}})
    started = local_container.started
    assert started[0]["image"] == "mcr.microsoft.com/playwright:v1.64.1-noble"
    assert started[0]["kind"] == "job" and started[0]["runner_image"] == "runner:24.04"


def test_starting_a_container_mounts_only_the_file_commands_and_puts_the_work_where_github_does(tmp_path):
    calls = []

    def docker(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:img\nPATH=/custom/bin:/usr/bin\nHOME=/root\n", "")
        if command[-2:] == ["-c", 'echo "$(id -u):$(id -g)"']:
            return subprocess.CompletedProcess(command, 0, "0:0\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    meta = tmp_path / "jobs" / "ci.yml-browser" / "meta"
    container = containers.start(name="cedar-local-ci-x", image="mcr.microsoft.com/playwright:v1.64.1-noble",
                                 kind="job", repository="cedar-sample", commands=meta,
                                 maven_repository=tmp_path / "m2", runner_image="runner:24.04",
                                 environment={}, runner=docker)
    run = calls[0]
    assert run[:2] == ["docker", "run"] and "--init" in run
    assert f"{meta}:/__w/_temp/_runner_file_commands" in run
    assert not any(str(tmp_path / "jobs" / "ci.yml-browser") + ":" in argument for argument in run
                   if not argument.startswith(str(meta)))
    assert "type=image,source=runner:24.04,target=/__t,image-subpath=opt/hostedtoolcache" in run
    assert "--cap-add" not in run
    assert run[-4:] == ["tail", "mcr.microsoft.com/playwright:v1.64.1-noble", "-f", "/dev/null"]
    assert (container.path, container.image_id, container.owner) == ("/custom/bin:/usr/bin", "sha256:img", "0:0")
    assert container.places.workspace == "/__w/cedar-sample/cedar-sample"
    assert container.places.temporary == "/__w/_temp" and container.home == "/github/home"
    prepared = next(command for command in calls if "mkdir -p" in " ".join(command))
    assert "--user" in prepared and "/__w/cedar-sample/cedar-sample" in prepared


def test_the_runner_image_keeps_github_s_paths():
    where = containers.places("runner", "cedar-cli")
    assert where.workspace == "/home/runner/work/cedar-cli/cedar-cli"
    assert where.temporary == "/home/runner/work/_temp" and where.tool_cache == "/opt/hostedtoolcache"


def test_the_runner_image_tag_follows_the_dockerfile_and_the_base():
    first = containers.runner_image_tag("24.04")
    assert first.startswith("cedar-local-ci-runner:24.04-") and first != containers.runner_image_tag("26.04")
    assert all(base.split("@")[1].startswith("sha256:") for base in containers.BASES.values())
    assert set(containers.RELEASES.values()) <= set(containers.BASES)


def test_a_sparse_checkout_holds_only_the_directories_it_names(tmp_path):
    home = tmp_path / "home"
    root = home / "cedar-sample"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("a\n")
    (root / "src").mkdir()
    (root / "src" / "b.txt").write_text("b\n")
    revision = repository(home, CONTAINER)
    runner = JobRunner(cedar_home=home, repository="cedar-sample", revision=revision, parent=None,
                       run_directory=tmp_path / "run", environment={"PATH": "/usr/bin:/bin"},
                       secrets={}, maven_repository=tmp_path / "m2", run_id="test")
    workspace = tmp_path / "job" / "workspace"
    (tmp_path / "job" / "tmp").mkdir(parents=True)
    workspace.mkdir()

    runner._checkout({"path": "sample", "sparse-checkout": "docs"}, workspace, SimpleNamespace(write=lambda text: None))

    assert (workspace / "sample" / "docs" / "a.md").is_file()
    assert (workspace / "sample" / "README.md").is_file()  # cone mode keeps the top-level files
    assert not (workspace / "sample" / "src").exists()

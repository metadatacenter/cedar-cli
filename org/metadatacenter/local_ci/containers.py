"""Linux containers for the job legs GitHub runs on Ubuntu.

A job reaches a Linux container in one of two ways. A job that names a `container:` runs in that
image, as GitHub runs it. Any other leg whose runner label is an Ubuntu release runs in local CI's
runner image when the registry's `linux` entry for the job asks for it. A leg whose commands behave
the same on macOS needs no container, so without that entry the leg runs on this machine. The entry
also gives the reason an arm64 Linux container here answers for GitHub's runner, which for
`ubuntu-latest` is an x86_64 virtual machine.

The workspace and the temporary directory sit where GitHub puts them, on the container's own file
system: `/home/runner/work` in the runner image, `/__w` in a job's own container. A directory
mounted from macOS would differ from GitHub's disk in case sensitivity, in the length of its paths
and in how a few system calls behave. Only the files a step writes its outputs and environment to,
and the script each step runs, are mounted from this machine, where local CI reads them. Checkout
and the artifact actions, which local CI carries out on this machine, copy what they produce into
the container and out of it.

Each `run:` step is one `docker exec`. Its environment travels in a file the step reads and deletes
before it starts, so no value, a secret included, appears on a command line.
"""
from __future__ import annotations

import hashlib
import posixpath
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from org.metadatacenter.local_ci.errors import LocalCIError
from org.metadatacenter.local_ci.expressions import UnsupportedExpression, interpolate, to_string

PLATFORM = "linux/arm64"
DOCKERFILE = Path(__file__).resolve().parents[3] / "assets" / "local-ci" / "runner.Dockerfile"

# The Ubuntu release behind each runner label, and the base image local CI builds it from.
# `ubuntu-latest` moved from 24.04 to 26.04 by 2026-11-04.
RELEASES = {"ubuntu-latest": "26.04", "ubuntu-26.04": "26.04", "ubuntu-26.04-arm": "26.04",
            "ubuntu-24.04": "24.04", "ubuntu-24.04-arm": "24.04"}
BASES = {
    "24.04": "ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852",
    "26.04": "ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c",
}

# Where the work directory, the tool cache and the home directory sit: in the runner image, where
# GitHub's runner keeps them, and in a job's own container, where GitHub mounts them.
RUNNER_WORK = "/home/runner/work"
RUNNER_TOOL_CACHE = "/opt/hostedtoolcache"
RUNNER_HOME = "/home/runner"
JOB_WORK = "/__w"
JOB_TOOL_CACHE = "/__t"
JOB_HOME = "/github/home"
PYTHONS = ("3.10", "3.12")
JOB_CONTAINER_KEYS = frozenset({"image", "env"})
LABEL = "org.metadatacenter.local-ci"

# Reads the NUL-separated NAME=VALUE entries of the file named by $1, deletes it, then runs the
# rest of the command line with those variables exported.
LOADER = """while IFS= read -r -d '' entry; do
  case "${entry%%=*}" in
    ''|[0-9]*|*[!A-Za-z0-9_]*) ;;
    *) export "$entry" ;;
  esac
done < "$1"
rm -f -- "$1"
shift
exec "$@"
"""

_BUILD_LOCK = threading.Lock()


def runs_on(definition: dict, matrix: dict) -> str | None:
    """The runner label a leg asks for, read with its matrix, or None when it cannot be read."""
    value = definition.get("runs-on")
    try:
        label = interpolate(value, {"matrix": dict(matrix)})
    except (UnsupportedExpression, KeyError, TypeError):
        return None
    return label.strip() if isinstance(label, str) else None


def runner_release(label: str | None) -> str | None:
    return RELEASES.get(label) if label else None


def job_container(definition: dict) -> dict | None:
    """A job's `container:` as a mapping, whichever of its two forms the workflow uses."""
    value = definition.get("container")
    if not value:
        return None
    return dict(value) if isinstance(value, dict) else {"image": value}


def runner_image_tag(release: str) -> str:
    digest = hashlib.sha256(DOCKERFILE.read_bytes() + BASES[release].encode("utf-8")).hexdigest()
    return f"cedar-local-ci-runner:{release}-{digest[:12]}"


def docker(arguments: list[str], environment: dict, *, check: bool = True, timeout: float | None = None,
           text_input: str | None = None, runner=subprocess.run):
    try:
        result = runner(["docker", *arguments], env=environment, text=True, capture_output=True,
                        check=False, timeout=timeout, input=text_input)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LocalCIError(f"docker {arguments[0]} did not finish: {error}") from error
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise LocalCIError(f"docker {' '.join(arguments[:2])} failed: {detail[-600:]}")
    return result


def ensure_runner_image(release: str, environment: dict, log, *, runner=subprocess.run) -> str:
    """The runner image for an Ubuntu release, built once from the Dockerfile when it is missing."""
    tag = runner_image_tag(release)
    with _BUILD_LOCK:
        present = docker(["image", "inspect", "--format", "{{.Id}}", tag], environment, check=False,
                         runner=runner)
        if present.returncode == 0:
            return tag
        log(f"   building the local CI runner image {tag}\n")
        built = docker(["build", "--platform", PLATFORM, "--build-arg", f"BASE={BASES[release]}",
                        "--tag", tag, "-"], environment, check=False,
                       text_input=DOCKERFILE.read_text(encoding="utf-8"), runner=runner)
        log((built.stdout or "") + (built.stderr or ""))
        if built.returncode:
            raise LocalCIError(f"could not build the local CI runner image {tag}")
    return tag


@dataclass(frozen=True)
class Places:
    """Where a leg's directories are, as its steps see them."""

    workspace: str
    temporary: str
    commands: str  # the file commands, GITHUB_OUTPUT among them, and each step's script
    tool_cache: str
    home: str


def places(kind: str, repository: str) -> Places:
    """GitHub's layout for a leg of a repository, in the runner image or a job's own container."""
    work, tool_cache, home = ((RUNNER_WORK, RUNNER_TOOL_CACHE, RUNNER_HOME) if kind == "runner"
                              else (JOB_WORK, JOB_TOOL_CACHE, JOB_HOME))
    return Places(f"{work}/{repository}/{repository}", f"{work}/_temp",
                  f"{work}/_temp/_runner_file_commands", tool_cache, home)


@dataclass
class Container:
    """One leg's running container, and how a step reaches it."""

    name: str
    image: str
    kind: str  # 'runner' for the runner image, 'job' for a job's own container
    places: Places
    path: str
    client_environment: dict
    image_id: str = ""
    owner: str = "0:0"
    ended: str = ""
    runner: object = field(default=subprocess.run, repr=False)

    @property
    def tool_cache(self) -> str:
        return self.places.tool_cache

    @property
    def home(self) -> str:
        return self.places.home

    def command(self, argv: list[str], *, cwd: str, env_file: Path) -> list[str]:
        """The docker exec that runs argv in cwd with the variables env_file holds."""
        return ["docker", "exec", "--workdir", cwd, self.name, "bash", "--noprofile", "--norc", "-c",
                LOADER, "local-ci", str(env_file), *argv]

    def run(self, argv: list[str], *, cwd: str | None = None, root: bool = False):
        """A short command in the container, outside any step, such as a setup action's check."""
        arguments = ["exec", *(["--workdir", cwd] if cwd else []), *(["--user", "0"] if root else []),
                     self.name, *argv]
        return docker(arguments, self.client_environment, check=False, runner=self.runner)

    def has(self, path: str) -> bool:
        return self.run(["test", "-e", path]).returncode == 0

    def is_directory(self, path: str) -> bool:
        return self.run(["test", "-d", path]).returncode == 0

    def read(self, path: str) -> str:
        result = self.run(["cat", path])
        if result.returncode:
            raise LocalCIError(f"the container has no file {path}")
        return result.stdout

    def copy_in(self, source: Path, destination: str) -> None:
        """Copy a directory's contents to a path in the container, owned by the container's user."""
        made = self.run(["mkdir", "-p", destination], root=True)
        if made.returncode:
            raise LocalCIError(f"could not make {destination} in the container: {made.stderr.strip()}")
        docker(["cp", f"{source}/.", f"{self.name}:{destination}"], self.client_environment,
               runner=self.runner)
        owned = self.run(["chown", "-R", self.owner, destination], root=True)
        if owned.returncode:
            raise LocalCIError(f"could not give {destination} to the container's user: "
                               f"{owned.stderr.strip()}")

    def copy_out(self, source: str, destination: Path) -> bool:
        """Copy a path in the container to one here that does not exist yet; False if it is absent."""
        if not self.has(source):
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        docker(["cp", f"{self.name}:{source}", str(destination)], self.client_environment,
               runner=self.runner)
        return True

    def end(self, reason: str) -> None:
        """Stop the container at once, as a step that ran past its time limit requires."""
        self.ended = reason
        docker(["rm", "--force", "--volumes", self.name], self.client_environment, check=False,
               runner=self.runner)

    def record(self) -> dict:
        return {"kind": self.kind, "image": self.image, "imageId": self.image_id, "platform": PLATFORM}


def write_environment(path: Path, environment: dict) -> None:
    """The variables a step runs with, as the loader reads them, readable by this user alone."""
    path.touch(mode=0o600)
    with path.open("w", encoding="utf-8") as handle:
        for name, value in environment.items():
            handle.write(f"{name}={value}\0")


def start(*, name: str, image: str, kind: str, repository: str, commands: Path,
          maven_repository: Path, runner_image: str | None, environment: dict,
          runner=subprocess.run) -> Container:
    """Start a leg's container, idle until its steps arrive, as GitHub starts a job container."""
    where = places(kind, repository)
    maven_repository.mkdir(parents=True, exist_ok=True)
    arguments = ["run", "--detach", "--init", "--name", name, "--platform", PLATFORM,
                 "--label", f"{LABEL}=1",
                 "--volume", f"{commands}:{where.commands}",
                 "--volume", f"{maven_repository}:{maven_repository}"]
    if kind == "runner":
        # GitHub's runner is a virtual machine in which sudo can mount a file system.
        arguments += ["--cap-add", "SYS_ADMIN"]
    else:
        if not runner_image:
            raise LocalCIError("a job container needs the runner image's tool cache")
        arguments += ["--mount", f"type=image,source={runner_image},target={JOB_TOOL_CACHE},"
                                 f"image-subpath={RUNNER_TOOL_CACHE.lstrip('/')}"]
    arguments += ["--entrypoint", "tail", image, "-f", "/dev/null"]
    docker(arguments, environment, runner=runner, timeout=1800)
    container = Container(name, image, kind, where, "", environment, runner=runner)
    try:
        inspected = docker(["container", "inspect", "--format",
                            "{{.Image}}\n{{range .Config.Env}}{{println .}}{{end}}", name],
                           environment, runner=runner)
        lines = inspected.stdout.splitlines()
        container.image_id = lines[0].strip() if lines else ""
        paths = [line[len("PATH="):] for line in lines[1:] if line.startswith("PATH=")]
        container.path = paths[0] if paths else "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        if container.run(["bash", "--version"]).returncode:
            raise LocalCIError(f"{image} has no bash, which local CI runs every step with")
        identity = container.run(["sh", "-c", 'echo "$(id -u):$(id -g)"'])
        if identity.returncode or ":" not in identity.stdout:
            raise LocalCIError(f"could not tell which user {image} runs as")
        container.owner = identity.stdout.strip()
        # The directories GitHub creates before the first step, owned by the user the steps run as.
        work = posixpath.dirname(posixpath.dirname(where.workspace))
        made = container.run(["sh", "-c", 'mkdir -p "$2" "$3" "$4" && chown "$1" "$5" "$(dirname "$2")" '
                                          '"$2" "$3" "$4"',
                              "local-ci", container.owner, where.workspace, where.temporary,
                              where.home, work], root=True)
        if made.returncode:
            raise LocalCIError(f"could not prepare the work directories in {image}: "
                               f"{(made.stderr or made.stdout).strip()}")
    except LocalCIError:
        container.end("it could not be prepared")
        raise
    return container


def container_name(run_id: str, slug: str) -> str:
    return f"cedar-local-ci-{run_id}-{hashlib.sha256(slug.encode('utf-8')).hexdigest()[:10]}"


def image_name(definition: dict, context: dict) -> str:
    spec = job_container(definition) or {}
    image = to_string(interpolate(spec.get("image"), context)).strip()
    if not image:
        raise LocalCIError("the job container names no image")
    return image

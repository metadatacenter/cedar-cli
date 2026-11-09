"""Every command the route runs, behind one object so a test can stand in for the world."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

from org.metadatacenter.npm_release_support.errors import NpmReleaseError


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def detail(self) -> str:
        text = (self.stderr or self.stdout or "").strip()
        return text.splitlines()[-1] if text else f"exit {self.returncode}"


class Tools:
    """Runs git, gh and npm for the route, and streams long commands into attempt logs.

    Short commands are captured, since their output is evidence the route reads. Gates and builds
    run for many minutes and write a log of their own, of which the route keeps only the path.
    """

    def __init__(self, environment: dict, *, sleeper=time.sleep, clock=time.monotonic, reporter=print):
        self.environment = environment
        self.sleep = sleeper
        self.clock = clock
        self.report = reporter

    # Captured commands -----------------------------------------------------------------------

    def run(self, argv: list[str], cwd: Path | None = None, *, check: bool = True) -> Result:
        try:
            completed = subprocess.run(
                argv, cwd=str(cwd) if cwd else None, env=self.environment,
                text=True, capture_output=True, check=False)
        except OSError as error:
            raise NpmReleaseError(f"cannot run {argv[0]}: {error}") from error
        result = Result(completed.returncode, completed.stdout or "", completed.stderr or "")
        if check and result.returncode:
            raise NpmReleaseError(f"{' '.join(argv[:3])} failed in {cwd or '.'}: {result.detail}")
        return result

    def git(self, repository: Path, *args: str, check: bool = True) -> str:
        return self.run(["git", *args], repository, check=check).stdout.strip()

    def git_result(self, repository: Path, *args: str) -> Result:
        return self.run(["git", *args], repository, check=False)

    def gh(self, *args: str, check: bool = True) -> Result:
        return self.run(["gh", *args], check=check)

    def gh_json(self, *args: str):
        result = self.gh(*args)
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError as error:
            raise NpmReleaseError(f"gh {' '.join(args[:2])} returned malformed JSON") from error

    def npm(self, cwd: Path | None, *args: str, check: bool = True) -> Result:
        return self.run(["npm", *args], cwd, check=check)

    # Logged commands -------------------------------------------------------------------------

    def logged(self, argv: list[str], cwd: Path, log: Path, *, heartbeat_seconds: float = 300) -> None:
        """Run a long command with its whole output kept in `log`, refusing a failure by name.

        A gate runs for many minutes with its output going to the log, so the operator is told
        every few minutes that it is still running rather than left looking at a silent terminal.
        """
        log.parent.mkdir(parents=True, exist_ok=True)
        command = " ".join(argv)
        started = self.clock()
        self.report(f"  running {command} (output in {log})")
        with log.open("ab") as stream:
            stream.write(f"\n$ (cd {cwd} && {command})\n".encode())
            stream.flush()
            try:
                process = subprocess.Popen(
                    argv, cwd=str(cwd), env=self.environment, stdout=stream, stderr=subprocess.STDOUT)
            except OSError as error:
                raise NpmReleaseError(f"cannot run {argv[0]}: {error}") from error
            try:
                while True:
                    try:
                        returncode = process.wait(timeout=heartbeat_seconds)
                        break
                    except subprocess.TimeoutExpired:
                        self.report(f"  still running after {_elapsed(self.clock() - started)}: {command}")
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        elapsed = _elapsed(self.clock() - started)
        if returncode:
            raise NpmReleaseError(f"{command} failed in {cwd} after {elapsed}; the output is in {log}")
        self.report(f"  passed in {elapsed}: {command}")

    # Registry --------------------------------------------------------------------------------

    def npm_view(self, spec: str, registry: str) -> dict | None:
        """One version's registry document, or None when the registry has no such version.

        An authentication failure can also answer E404, so a caller asks `whoami` first.
        """
        result = self.npm(None, "view", spec, "--json", f"--registry={registry}", check=False)
        if result.returncode:
            text = f"{result.stdout}\n{result.stderr}"
            if "E404" in text or "404 Not Found" in text:
                return None
            raise NpmReleaseError(f"npm view {spec} failed: {result.detail}")
        if not result.stdout.strip():
            return None
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise NpmReleaseError(f"npm view {spec} returned malformed JSON") from error
        if isinstance(value, list):
            value = value[-1] if value else None
        return value if isinstance(value, dict) else None

    def download(self, url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except (OSError, urllib.error.URLError) as error:
            raise NpmReleaseError(f"cannot download {url}: {error}") from error

    def reachable(self, url: str) -> bool:
        request = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return 200 <= response.status < 300
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 404, 410):
                return False
            raise NpmReleaseError(f"cannot check {url}: HTTP {error.code}") from error
        except (OSError, urllib.error.URLError) as error:
            raise NpmReleaseError(f"cannot check {url}: {error}") from error

    # Time ------------------------------------------------------------------------------------

    @staticmethod
    def local_now() -> dt.datetime:
        return dt.datetime.now().astimezone()


def _elapsed(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.1f} min"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

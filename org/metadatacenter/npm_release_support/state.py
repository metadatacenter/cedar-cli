"""The ledger of an npm release run: one file per run, and a pointer naming the active one."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import datetime as dt
import fcntl
import json
from pathlib import Path

from org.metadatacenter.npm_release_support.errors import NpmReleaseError
from org.metadatacenter.util.InvocationContext import invocation_environment

SCHEMA_VERSION = 1


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


class NpmReleaseState:
    """Owns the run ledgers under one state root.

    A ledger is replaced atomically after every stage, so a reader never sees half a record and a
    run interrupted anywhere resumes from the last stage it proved.
    """

    def __init__(self, root: Path | None = None, environment=None):
        environment = invocation_environment() if environment is None else environment
        configured = environment.get("CEDAR_NPM_RELEASE_STATE_DIR")
        self.root = root or (
            Path(configured).expanduser() if configured else Path.home() / ".cedar" / "npm-releases")

    @property
    def current_path(self) -> Path:
        return self.root / "current.json"

    def run_path(self, run_id: str) -> Path:
        return self.root / "runs" / f"{run_id}.json"

    def attempt_directory(self, run_id: str, package: str) -> Path:
        path = self.root / "attempts" / run_id / package
        path.mkdir(parents=True, exist_ok=True)
        return path

    @contextmanager
    def exclusive(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "release.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise NpmReleaseError(
                    "another npm release command is running; inspect it with "
                    "cedarcli release npm status") from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(_json_bytes(value))
        temporary.replace(path)

    def active(self) -> dict | None:
        """The pointer to the run in progress, or None when the last run has concluded."""
        if not self.current_path.exists():
            return None
        try:
            current = json.loads(self.current_path.read_bytes())
        except json.JSONDecodeError as error:
            raise NpmReleaseError(f"invalid npm release state at {self.current_path}") from error
        return None if current.get("concludedAt") else current

    def start(self, run: dict) -> Path:
        active = self.active()
        if active:
            raise NpmReleaseError(
                f"npm release run {active['run']} is still active; continue it with "
                "cedarcli release npm resume, or close it with cedarcli release npm abandon")
        path = self.run_path(run["id"])
        if path.exists():
            raise NpmReleaseError(f"npm release state already exists at {path}")
        record = copy.deepcopy(run)
        record["schemaVersion"] = SCHEMA_VERSION
        record["startedAt"] = now()
        self._write(path, record)
        self._write(self.current_path, {
            "schemaVersion": SCHEMA_VERSION, "run": run["id"], "ledger": str(path),
            "startedAt": record["startedAt"]})
        return path

    def read(self, *, include_concluded: bool = False) -> tuple[dict, Path]:
        if not self.current_path.exists():
            raise NpmReleaseError("there is no npm release run")
        try:
            current = json.loads(self.current_path.read_bytes())
        except json.JSONDecodeError as error:
            raise NpmReleaseError(f"invalid npm release state at {self.current_path}") from error
        if current.get("concludedAt") and not include_concluded:
            raise NpmReleaseError(
                f"npm release run {current['run']} concluded as {current.get('conclusion')}; "
                "there is nothing to resume")
        path = Path(current.get("ledger", ""))
        if path.parent.resolve() != (self.root / "runs").resolve() or not path.is_file():
            raise NpmReleaseError(f"the active npm release ledger is missing or misplaced: {path}")
        try:
            run = json.loads(path.read_bytes())
        except json.JSONDecodeError as error:
            raise NpmReleaseError(f"invalid npm release ledger at {path}") from error
        if run.get("id") != current.get("run"):
            raise NpmReleaseError("the npm release pointer and its ledger disagree")
        return run, path

    def save(self, run: dict) -> None:
        self._write(self.run_path(run["id"]), run)

    def conclude(self, run: dict, conclusion: str) -> None:
        run["concludedAt"] = now()
        run["conclusion"] = conclusion
        self.save(run)
        current = json.loads(self.current_path.read_bytes())
        current["concludedAt"] = run["concludedAt"]
        current["conclusion"] = conclusion
        self._write(self.current_path, current)

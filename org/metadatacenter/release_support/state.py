"""CEDAR release state."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import copy
import datetime as dt
import fcntl
import json
import os
import shutil
from org.metadatacenter.release_support.errors import (
    ReleaseError,
)
from org.metadatacenter.release_support.hashes import (
    _json_bytes,
)


class ReleaseState:
    def __init__(self, root: Path | None = None, environment=None):
        environment = os.environ if environment is None else environment
        configured = environment.get("CEDAR_RELEASE_STATE_DIR")
        self.root = root or (
            Path(configured).expanduser()
            if configured
            else Path.home() / ".cedar" / "train-releases"
        )

    @contextmanager
    def exclusive(self):
        """Own all release mutations until completion, failure, or process exit.

        Keep the lock file in place: unlinking it would let another process lock a
        different inode. Status readers use atomically replaced manifests without
        acquiring this lock. File descriptors are not inherited by child commands.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "release.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ReleaseError(
                    "Another release command is running; use cedarcli release status "
                    "to inspect it and wait for it to finish before modifying the release."
                ) from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @property
    def current_path(self) -> Path:
        return self.root / "current.json"

    def manifest_path(self, release_version: str) -> Path:
        return self.root / "releases" / f"{release_version}.json"

    def _next_manifest_path(self, release_version: str) -> Path:
        path = self.manifest_path(release_version)
        if not path.exists():
            return path
        current = self.read_current()
        if (
            current.get("releaseVersion") != release_version
            or current.get("conclusion") != "abandoned"
            or not current.get("concludedAt")
        ):
            raise ReleaseError(f"release state already exists at {path}")
        attempt = 2
        while True:
            candidate = path.with_name(f"{release_version}-attempt-{attempt:03d}.json")
            if not candidate.exists():
                return candidate
            attempt += 1

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(_json_bytes(value))
        temporary.replace(path)

    def start(self, manifest: dict) -> Path:
        if self.current_path.exists():
            current = self.read_current()
            if not current.get("concludedAt"):
                raise ReleaseError(
                    f"release {current['releaseVersion']} is already active; "
                    "use cedarcli release status"
                )
        path = self._next_manifest_path(manifest["releaseVersion"])
        active = copy.deepcopy(manifest)
        active["phase"] = "started"
        active["startedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self._write(path, active)
        self._write(self.current_path, {
            "schemaVersion": 1,
            "releaseVersion": active["releaseVersion"],
            "manifest": str(path),
            "startedAt": active["startedAt"],
        })
        # A release attempt is an operational workspace, not an archive. Once the
        # pointer names this release, older workspaces and ledgers are baggage and
        # can only make the next train less reliable by consuming disk.
        self._prune_obsolete()
        return path

    def _prune_obsolete(self) -> dict[str, list[str]]:
        """Keep only the release named by current.json and its active attempt.

        Paths come only from the state root and the current manifest. A malformed
        pointer is refused before anything is removed.
        """
        current = self.read_current()
        manifest = Path(current.get("manifest", "")).expanduser().resolve()
        releases = (self.root / "releases").resolve()
        if manifest.parent != releases or not manifest.is_file():
            raise ReleaseError(f"active release manifest is outside the state root: {manifest}")

        try:
            active = json.loads(manifest.read_bytes())
        except json.JSONDecodeError as error:
            raise ReleaseError(f"invalid release manifest at {manifest}") from error
        if active.get("releaseVersion") != current.get("releaseVersion"):
            raise ReleaseError("active release pointer and manifest disagree")

        protected_attempt = None
        workspace = active.get("frontendPreparation", {}).get("workspace")
        if workspace:
            candidate = Path(workspace).expanduser().resolve().parent
            attempts = (self.root / "attempts").resolve()
            # Only paths inside this state root can need protection: enumeration
            # below never follows or removes anything outside it.
            if candidate.parent.parent == attempts:
                protected_attempt = candidate

        removed_attempts: list[str] = []
        attempts_root = self.root / "attempts"
        if attempts_root.is_dir():
            for version_dir in sorted(attempts_root.iterdir()):
                if not version_dir.is_dir() or version_dir.is_symlink():
                    continue
                for attempt in sorted(version_dir.iterdir()):
                    if not attempt.is_dir() or attempt.is_symlink():
                        continue
                    if protected_attempt is not None and attempt.resolve() == protected_attempt:
                        continue
                    shutil.rmtree(attempt)
                    removed_attempts.append(str(attempt))
                try:
                    version_dir.rmdir()
                except OSError:
                    pass

        removed_ledgers: list[str] = []
        if releases.is_dir():
            for ledger in sorted(releases.iterdir()):
                if not ledger.is_file() or ledger.resolve() == manifest:
                    continue
                ledger.unlink()
                removed_ledgers.append(str(ledger))
        return {"attempts": removed_attempts, "ledgers": removed_ledgers}

    def conclude(self, outcome: str = "accepted") -> None:
        """Record that the active release has finished, so it no longer holds the slot.

        Nothing used to mark a release finished, so the pointer at current.json kept naming
        it forever and the next release could not start. The pointer stays where it is,
        stamped rather than deleted, so status still has the last release to show; what
        changes is that start no longer treats it as in progress.
        """
        current = self.read_current()
        changed = False
        if not current.get("concludedAt"):
            current["concludedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
            changed = True
        if not current.get("conclusion"):
            current["conclusion"] = outcome
            changed = True
        elif current["conclusion"] != outcome:
            raise ReleaseError(
                f"release is already concluded as {current['conclusion']}, not {outcome}"
            )
        if changed:
            self._write(self.current_path, current)

    def read_current(self) -> dict:
        if not self.current_path.exists():
            raise ReleaseError("there is no active train-backed release")
        try:
            current = json.loads(self.current_path.read_bytes())
        except json.JSONDecodeError as error:
            raise ReleaseError(f"invalid release state at {self.current_path}") from error
        return current

    def read_current_manifest(self) -> tuple[dict, Path]:
        current = self.read_current()
        path = Path(current.get("manifest", ""))
        if not path.is_file():
            raise ReleaseError(f"active release manifest is missing: {path}")
        try:
            manifest = json.loads(path.read_bytes())
        except json.JSONDecodeError as error:
            raise ReleaseError(f"invalid release manifest at {path}") from error
        if manifest.get("releaseVersion") != current.get("releaseVersion"):
            raise ReleaseError("active release pointer and manifest disagree")
        return manifest, path

    def update_current_manifest(self, changes: dict) -> tuple[dict, Path]:
        manifest, path = self.read_current_manifest()
        manifest.update(copy.deepcopy(changes))
        self._write(path, manifest)
        return manifest, path

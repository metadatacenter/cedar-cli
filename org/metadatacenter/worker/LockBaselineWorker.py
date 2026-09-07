"""Review and refresh the npm lock baselines a build train is bound to."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess

from rich.console import Console
from rich.table import Column, Table
from rich.text import Text

from org.metadatacenter.release_train import ToolchainResolver
from org.metadatacenter.util.Util import Util

console = Console()

SEVERITIES = ("low", "moderate", "high", "critical")
CONFIG_PATH = Path("cedar-development") / "ops" / "frontend-train.json"


@dataclass(frozen=True)
class LockBaseline:
    """One reviewed lockfile: the digest and advisory counts the train expects, and what is there."""

    repository: str
    lock: str
    recorded_sha256: str
    actual_sha256: str | None
    counts: dict

    @property
    def state(self):
        if self.actual_sha256 is None:
            return "missing"
        return "current" if self.actual_sha256 == self.recorded_sha256 else "stale"

    @property
    def identity(self):
        return f"{self.repository}:{self.lock}"


def _counts_text(counts):
    return "/".join(str(counts.get(severity, 0)) for severity in SEVERITIES)


class LockBaselineWorker:
    """The train refuses a lock whose digest moved; this says which ones did, and re-reviews them.

    A baseline binds the advisory counts an operator reviewed to the exact dependency graph that
    produced them, so a changed graph stops a train until someone looks at it again. The looking
    used to be a hand procedure per lock: compute the digest, run npm audit, edit the JSON. This
    does the mechanical part for every stale lock at once and leaves the review and the commit to
    the operator.
    """

    @staticmethod
    def _cedar_home():
        cedar_home = Util.cedar_home or os.environ.get("CEDAR_HOME")
        if not cedar_home:
            raise ValueError("CEDAR_HOME is not set")
        return Path(cedar_home)

    @classmethod
    def _config(cls, cedar_home):
        path = cedar_home / CONFIG_PATH
        try:
            raw = path.read_text(encoding="utf-8")
            config = json.loads(raw)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot read train frontend configuration {path}: {error}") from error
        baselines = config.get("auditBaselines")
        if not isinstance(baselines, list) or not baselines:
            raise ValueError(f"{path} declares no npm audit baselines")
        return path, raw, config

    @classmethod
    def survey(cls, repositories=None):
        cedar_home = cls._cedar_home()
        _path, _raw, config = cls._config(cedar_home)
        wanted = set(repositories or [])
        surveyed = []
        for baseline in config["auditBaselines"]:
            repository = baseline.get("repository")
            lock = baseline.get("lock")
            if not isinstance(repository, str) or not isinstance(lock, str):
                raise ValueError("frontend-train.json contains an unnamed npm audit baseline")
            if wanted and repository not in wanted:
                continue
            path = cedar_home / repository / lock
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            surveyed.append(LockBaseline(
                repository, lock, str(baseline.get("sha256", "")), actual,
                dict(baseline.get("vulnerabilities") or {}),
            ))
        if wanted and not surveyed:
            raise ValueError(
                "no npm audit baseline belongs to " + ", ".join(sorted(wanted)))
        return surveyed

    @staticmethod
    def audit_counts(directory, runner=None, environment=None):
        """The advisory counts npm reports for the lock in this directory, by severity.

        npm audit exits non-zero whenever it finds an advisory, so the exit status says nothing
        about whether the command worked; the JSON it printed does.
        """
        runner = runner or subprocess.run
        try:
            result = runner(
                ["npm", "audit", "--json"],
                cwd=str(directory),
                env=environment if environment is not None else os.environ,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            raise ValueError(f"cannot run npm audit in {directory}: {error}") from error
        try:
            payload = json.loads(result.stdout or "")
            vulnerabilities = payload["metadata"]["vulnerabilities"]
            return {severity: int(vulnerabilities[severity]) for severity in SEVERITIES}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise ValueError(
                f"npm audit in {directory} reported no advisory counts"
                + (f": {detail[-1]}" if detail else "")) from error

    @classmethod
    def _activate_toolchain(cls):
        """The counts are only comparable when the same npm produced them, so use the estate's Node."""
        for note in ToolchainResolver(os.environ).resolve():
            console.print(f"Toolchain: {note}")

    @classmethod
    def report(cls, show_all=False, repositories=None):
        try:
            baselines = cls.survey(repositories)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            return 1
        shown = [baseline for baseline in baselines if show_all or baseline.state != "current"]
        if shown:
            table = Table(
                Column("Lock", overflow="fold"), Column("State"), Column("Recorded"),
                Column("In checkout"), Column("low/mod/high/crit"),
                title="npm lock baselines the train is bound to",
            )
            styles = {"current": "green", "stale": "yellow", "missing": "red"}
            for baseline in shown:
                table.add_row(
                    baseline.identity,
                    Text(baseline.state, style=styles[baseline.state]),
                    baseline.recorded_sha256[:12],
                    (baseline.actual_sha256 or "absent")[:12],
                    _counts_text(baseline.counts),
                )
            console.print(table)
        stale = [baseline for baseline in baselines if baseline.state != "current"]
        current = len(baselines) - len(stale)
        console.print(f"{current} current, {len(stale)} stale or missing of {len(baselines)} baselines")
        if stale:
            console.print(
                "A train would refuse these locks. Review each changed graph, then run "
                "cedarcli publish baselines --refresh and commit ops/frontend-train.json in "
                "cedar-development.")
            return 1
        console.print("[green]Every reviewed lock matches its baseline.[/green]")
        return 0

    @classmethod
    def refresh(cls, repositories=None, auditor=None):
        """Recompute the digest and advisory counts of every stale lock, and write them for review."""
        try:
            cedar_home = cls._cedar_home()
            path, raw, config = cls._config(cedar_home)
            baselines = cls.survey(repositories)
        except ValueError as error:
            console.print(f"[red]{error}[/red]")
            return 1
        stale = [baseline for baseline in baselines if baseline.state == "stale"]
        missing = [baseline for baseline in baselines if baseline.state == "missing"]
        for baseline in missing:
            console.print(f"[red]{baseline.identity} is absent from this checkout; nothing to review[/red]")
        if not stale:
            console.print("Every reviewed lock matches its baseline; nothing to refresh.")
            return 1 if missing else 0
        if auditor is None:
            cls._activate_toolchain()
            auditor = cls.audit_counts
        refreshed = []
        for baseline in stale:
            lock_path = cedar_home / baseline.repository / baseline.lock
            try:
                counts = auditor(lock_path.parent)
            except ValueError as error:
                console.print(f"[red]{error}[/red]")
                return 1
            for entry in config["auditBaselines"]:
                if entry.get("repository") == baseline.repository and entry.get("lock") == baseline.lock:
                    entry["sha256"] = baseline.actual_sha256
                    entry["vulnerabilities"] = counts
            refreshed.append((baseline, counts))
        path.write_text(
            json.dumps(config, indent=2) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
        for baseline, counts in refreshed:
            console.print(
                f"{baseline.identity}: digest {baseline.recorded_sha256[:12]} -> "
                f"{baseline.actual_sha256[:12]}, counts {_counts_text(baseline.counts)} -> "
                f"{_counts_text(counts)}", soft_wrap=True)
        console.print(
            f"Rewrote {len(refreshed)} baseline(s) in {path}. Review the diff, then commit it in "
            "cedar-development; the dispatch preflight reads the committed file.", soft_wrap=True)
        return 0

"""Every repository state an estate checkout can be in, put to every gate that reads one.

Each gate reads repository state its own way: the smoke record, the train's survey of open work
and alignment, a resumed train, the release, `git status`, the version check and the component pin
check. Separately written, they disagreed. A run on a feature branch recorded `develop` as tested,
an untracked source file went into the jar of a repository the record called clean, and a
repository absent from this machine got three verdicts from three gates.

Two invariants are asked of every state. The record never certifies code the stack did not run:
a repository it names as tested and clean has exactly that commit checked out, with nothing
modified or added. And the gates agree: a new train passes its local checks only when the source it
would capture is one a resumed train and a release would accept the same record for.

The repositories are real git repositories with a bare remote, and each state is produced the way
it arises in a workspace. Only the stack and the smoke tiers are stood in for.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from org.metadatacenter import smoke_gate
from org.metadatacenter.model.GitSyncState import GitSyncState
from org.metadatacenter.model.VersionReport import VersionReport
from org.metadatacenter.model.VersionType import VersionType
from org.metadatacenter.release_support.preflight import ReleasePreflight
from org.metadatacenter.train_support import preflight as train_preflight
from org.metadatacenter.train_support import survey
from org.metadatacenter.util.ComponentFreshness import ComponentState
from org.metadatacenter.util.GitSync import GitSync
from org.metadatacenter.util.RepoResultTriple import RepoResultTriple
from org.metadatacenter.util.ResultTable import ResultTable
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.ComponentWorker import ComponentWorker
from org.metadatacenter.worker.GitWorker import GitWorker

SUBJECT = "cedar-subject"
OTHER = "cedar-other"
REPOSITORIES = (SUBJECT, OTHER)
IDENTITY = ("-c", "user.email=t@example.org", "-c", "user.name=Test")
HEALTHY = ("service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"
           "ui-main\t201\t4200\tup\thealthy\t-\t0\n")


def git(cwd, *arguments) -> str:
    completed = subprocess.run(["git", *IDENTITY, *arguments], cwd=cwd, check=True,
                               capture_output=True, text=True)
    return completed.stdout.strip()


class Estate:
    """A CEDAR_HOME of two train repositories, each cloned from a bare remote of its own."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "CEDAR"
        ops = self.home / "cedar-development" / "ops"
        (ops / "e2e").mkdir(parents=True)
        (ops / "e2e" / "package.json").write_text("{}", encoding="utf-8")
        (ops / "build-train.json").write_text(json.dumps({"repositories": list(REPOSITORIES)}),
                                              encoding="utf-8")
        for name in REPOSITORIES:
            remote = root / "remotes" / f"{name}.git"
            git(root, "init", "--bare", "--initial-branch=develop", str(remote))
            producer = self.producer(name)
            git(root, "clone", str(remote), str(producer))
            self.commit(producer, "first")
            git(producer, "push", "-u", "origin", "develop")
            git(root, "clone", str(remote), str(self.home / name))
        GitSync.clear_cache()

    def producer(self, name: str) -> Path:
        """Somebody else's clone, which pushes while this workspace is not looking."""
        return self.root / "producers" / name

    def checkout(self, name: str = SUBJECT) -> Path:
        return self.home / name

    @staticmethod
    def commit(repository: Path, text: str, path: str = "src/Main.java") -> None:
        target = repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        git(repository, "add", path)
        git(repository, "commit", "-m", text)

    def push_elsewhere(self, name: str = SUBJECT) -> None:
        producer = self.producer(name)
        self.commit(producer, "pushed from elsewhere")
        git(producer, "push", "origin", "develop")

    def remote_heads(self) -> dict[str, str]:
        """What GitHub develop holds for every train repository, which is what a train captures."""
        return {name: git(self.root, "ls-remote", str(self.root / "remotes" / f"{name}.git"),
                          "refs/heads/develop").split()[0] for name in REPOSITORIES}


# --------------------------------------------------------------------------------------------------
# The states, each produced the way it arises
# --------------------------------------------------------------------------------------------------


def dirty_tracked(estate: Estate) -> None:
    (estate.checkout() / "src" / "Main.java").write_text("edited\n", encoding="utf-8")


def dirty_untracked(estate: Estate) -> None:
    (estate.checkout() / "src" / "Extra.java").write_text("class Extra {}\n", encoding="utf-8")


def ahead(estate: Estate) -> None:
    estate.commit(estate.checkout(), "committed here, not pushed")


def behind(estate: Estate) -> None:
    estate.push_elsewhere()
    git(estate.checkout(), "fetch")


def diverged(estate: Estate) -> None:
    estate.push_elsewhere()
    estate.commit(estate.checkout(), "committed here, not pushed")
    git(estate.checkout(), "fetch")


def stale_fetch(estate: Estate) -> None:
    estate.push_elsewhere()


def detached_at_develop(estate: Estate) -> None:
    git(estate.checkout(), "checkout", "--detach", "develop")


def detached_elsewhere(estate: Estate) -> None:
    checkout = estate.checkout()
    git(checkout, "checkout", "-b", "experiment")
    estate.commit(checkout, "an experiment")
    git(checkout, "checkout", "--detach", "experiment")


def other_branch(estate: Estate) -> None:
    checkout = estate.checkout()
    git(checkout, "checkout", "-b", "feature")
    estate.commit(checkout, "feature work")


def missing(estate: Estate) -> None:
    shutil.rmtree(estate.checkout())


def no_upstream(estate: Estate) -> None:
    git(estate.checkout(), "branch", "--unset-upstream", "develop")


STATES = {
    "clean": lambda estate: None,
    "dirty tracked": dirty_tracked,
    "dirty untracked": dirty_untracked,
    "ahead": ahead,
    "behind": behind,
    "diverged": diverged,
    "stale fetch": stale_fetch,
    "detached at develop": detached_at_develop,
    "detached elsewhere": detached_elsewhere,
    "other branch": other_branch,
    "missing": missing,
    "no upstream": no_upstream,
}

# What `git status` must ask of the operator for each state, or None where git cannot tell.
# A stale fetch is invisible until a fetch; a checkout on another branch is a legitimate place for
# a repository git status knows nothing about; a repository with no upstream has nothing to compare.
STATUS_SUGGESTIONS = {
    "clean": None,
    "dirty tracked": "Add, Commit, Push",
    "dirty untracked": "Add, Commit, Push",
    "ahead": "Push",
    "behind": "Pull",
    "diverged": "Pull with merge or rebase, then push",
    "stale fetch": None,
    "detached at develop": "Check out develop",
    "detached elsewhere": "Check out develop",
    "other branch": None,
    "no upstream": None,
}


class StackStandIn:
    """Answers the controller and the smoke tiers, and hands every git command to git."""

    def __init__(self, reports: Path):
        self.reports = reports

    def __call__(self, args, **kwargs):
        if args and str(args[0]).endswith("cedar-services.sh"):
            return SimpleNamespace(returncode=0, stdout=HEALTHY, stderr="")
        if args and args[0] == "npm":
            for argument in args:
                if argument.startswith("--report="):
                    Path(argument.split("=", 1)[1]).write_text(json.dumps(
                        {"runId": "r", "verdict": "PASS", "inventoryMatched": True}), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return subprocess.run(args, **kwargs)


class RepositoryStateMatrix(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()

    def tearDown(self):
        self.directory.cleanup()
        GitSync.clear_cache()

    def estate_in(self, state: str) -> Estate:
        estate = Estate(self.root / state.replace(" ", "-"))
        STATES[state](estate)
        GitSync.clear_cache()
        return estate

    def record(self, estate: Estate) -> dict | None:
        """Run `cedarcli test e2e` against the estate, and return what it recorded, if anything."""
        reports = smoke_gate.reports_dir(estate.home)
        with patch.object(smoke_gate.console, "print"), \
                patch.object(smoke_gate.shutil, "which", return_value="/usr/bin/npm"):
            smoke_gate.run_smoke(estate.home, runner=StackStandIn(reports),
                                 environment={"PATH": os.environ.get("PATH", "")})
        latest = reports / "latest.json"
        return json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else None

    @staticmethod
    def refusal(check) -> str | None:
        """Why a gate refuses, or None when it passes."""
        try:
            findings = check()
        except ValueError as error:
            return str(error)
        return "; ".join(findings) if findings else None

    def verdicts(self, estate: Estate) -> dict[str, str | None]:
        captured = estate.remote_heads()
        with patch.object(Util, "cedar_home", str(estate.home)):
            new_train = (self.refusal(survey._open_work)
                         or self.refusal(survey._source_alignment)
                         or self.refusal(lambda: train_preflight._smoke_gate_preflight(None)))
            resumed = self.refusal(
                lambda: train_preflight._smoke_gate_preflight({"repositories": captured}))
            release = self.refusal(lambda: [finding.message for finding in ReleasePreflight.check_smoke_gate(
                SimpleNamespace(manifest={"sourceRepositories": captured},
                                environment={"CEDAR_HOME": str(estate.home)}))])
        return {"new train": new_train, "resumed train": resumed, "release": release}

    def check_record_is_honest(self, estate: Estate, report: dict | None) -> None:
        if report is None:
            return
        tested = report["sources"].get(SUBJECT)
        if tested is None or SUBJECT in report["dirty"]:
            return
        checkout = estate.checkout()
        self.assertEqual(tested, git(checkout, "rev-parse", "HEAD"),
                         "the record names a commit the stack was not running")
        self.assertEqual("", git(checkout, "status", "--porcelain"),
                         "the record calls a checkout clean that holds work its commit does not")

    def check(self, state: str) -> None:
        estate = self.estate_in(state)
        report = self.record(estate)
        self.check_record_is_honest(estate, report)
        verdicts = self.verdicts(estate)
        passes = {gate for gate, refusal in verdicts.items() if refusal is None}
        self.assertIn(passes, (set(), set(verdicts)),
                      f"the gates disagree about one record: {json.dumps(verdicts, indent=2)}")
        if state in STATUS_SUGGESTIONS:
            self.check_status(estate, state)

    def check_status(self, estate: Estate, state: str) -> None:
        checkout = estate.checkout()
        output = subprocess.run(["git", "status"], cwd=checkout, capture_output=True, text=True,
                                env={**os.environ, "LC_ALL": "C"}).stdout.strip()
        result = ResultTable(["Repo", "Output", "Error"], True)
        result.add_result(RepoResultTriple(SimpleNamespace(name=SUBJECT), output, "", 0))
        rows = []
        worker = GitWorker()
        with patch.object(GitWorker, "register_active_repo",
                          side_effect=lambda triple, table, active, suggestion: rows.append(suggestion)), \
                patch("org.metadatacenter.worker.GitWorker.console"):
            worker.render_status_table(result)
        expected = STATUS_SUGGESTIONS[state]
        self.assertEqual([expected] if expected else [], rows, output)


def cells() -> None:
    for state in STATES:
        name = "test_" + state.replace(" ", "_")
        setattr(RepositoryStateMatrix, name, lambda self, s=state: self.check(s))


cells()


class VersionTargetMatrix(unittest.TestCase):
    """The version check's target follows the current checkouts, however many clones lag."""

    @staticmethod
    def entry(report, name, version, behind):
        sync = GitSyncState.tracking("develop", 0, 1 if behind else 0, 0)
        report.add(SimpleNamespace(name=name, allow_different_version=False), "", "pom.xml",
                   VersionType.POM_OWN, version, sync)

    def check(self, behind_count: int, current_count: int) -> None:
        report = VersionReport()
        for index in range(behind_count):
            self.entry(report, f"behind-{index}", "2.9.16", True)
        for index in range(current_count):
            self.entry(report, f"current-{index}", "2.9.17", False)
        report.summarize()
        self.assertEqual("2.9.17", report.version_candidate)
        self.assertEqual(0, report.cnt_nok, "a current checkout was failed for holding the new version")
        self.assertEqual(behind_count, report.cnt_stale)

    def test_a_minority_behind(self):
        self.check(1, 3)

    def test_a_tie(self):
        self.check(2, 2)

    def test_a_majority_behind(self):
        self.check(3, 1)


class FetchAgeMatrix(unittest.TestCase):
    """A clone that has never fetched, or a worktree, still says how old its view of the remote is."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.estate = Estate(Path(self.directory.name).resolve())

    def tearDown(self):
        self.directory.cleanup()
        GitSync.clear_cache()

    def test_a_clone_that_never_fetched(self):
        checkout = self.estate.checkout()
        self.assertFalse((checkout / ".git" / "FETCH_HEAD").exists())
        self.assertIsNotNone(GitSync.state_for_dir(str(checkout)).fetch_age_seconds)

    def test_a_worktree(self):
        checkout = self.estate.checkout()
        git(checkout, "fetch")
        worktree = Path(self.directory.name).resolve() / "worktree"
        git(checkout, "worktree", "add", "-b", "elsewhere", str(worktree))
        self.assertTrue((worktree / ".git").is_file())
        self.assertIsNotNone(GitSync.state_for_dir(str(worktree)).fetch_age_seconds)


class ComponentCloneMatrix(unittest.TestCase):
    """A host's pin on a component, against every state the component's own clone can be in."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.estate = Estate(Path(self.directory.name).resolve())
        self.component = self.estate.checkout(SUBJECT)

    def tearDown(self):
        self.directory.cleanup()
        GitSync.clear_cache()

    def verdict(self, commit: str) -> ComponentState:
        GitSync.clear_cache()
        version = f"2.0.16-dev.20260915.{commit[:12]}"
        finding = ComponentWorker._evaluate_pin("cedar-host", f"@scope/{SUBJECT}", version,
                                                {SUBJECT: self.component})
        return finding.state

    def test_a_pin_on_the_head(self):
        self.assertEqual(ComponentState.CURRENT, self.verdict(git(self.component, "rev-parse", "develop")))

    def test_a_pin_the_clone_has_fetched_and_not_pulled(self):
        # CI publishes every push to develop, so the pin can name a commit this clone has only
        # fetched. The pin is sound; the clone is behind.
        self.estate.push_elsewhere(SUBJECT)
        git(self.component, "fetch")
        pinned = git(self.component, "rev-parse", "origin/develop")
        self.assertEqual(ComponentState.UNRESOLVED, self.verdict(pinned))

    def test_a_pin_a_clone_that_has_not_fetched_cannot_see(self):
        self.estate.push_elsewhere(SUBJECT)
        pinned = git(self.estate.producer(SUBJECT), "rev-parse", "develop")
        old = self.component / ".git" / "FETCH_HEAD"
        packed = self.component / ".git" / "packed-refs"
        for marker in (old, packed):
            if marker.exists():
                os.utime(marker, (0, 0))
        self.assertEqual(ComponentState.UNRESOLVED, self.verdict(pinned))

    def test_a_pin_on_a_commit_no_develop_holds(self):
        git(self.component, "checkout", "-b", "rewritten")
        self.estate.commit(self.component, "never on develop")
        pinned = git(self.component, "rev-parse", "HEAD")
        git(self.component, "checkout", "develop")
        git(self.component, "fetch")
        self.assertEqual(ComponentState.DIVERGED, self.verdict(pinned))

    def test_a_pin_absent_from_a_clone_that_just_fetched(self):
        git(self.component, "fetch")
        self.assertEqual(ComponentState.DIVERGED, self.verdict("0123456789ab" + "0" * 28))


if __name__ == "__main__":
    unittest.main()

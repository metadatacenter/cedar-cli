import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import release_train
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.follow_through import (
    BASELINES,
    baseline_count_changes,
    follow_through_steps,
    run_follow_through,
)

REFRESHED = """cedar-workspace:package-lock.json: digest 38b67de173f6 -> 2eac85f37699, counts 0/0/0/0 -> 0/0/0/0
cedar-template-editor:package-lock.json: digest 1b3e44ef7c22 -> c37c8309c73c, counts 4/13/32/9 -> 4/13/32/9
Rewrote 2 baseline(s) in ops/frontend-train.json.
"""


class Recorder:
    """Stands in for the CLI and git, answering each command as the test says."""

    def __init__(self, codes=None, refresh=REFRESHED, dirty_before=False, dirty_after=True):
        self.commands, self.git_calls = [], []
        self.codes = codes or {}
        self.refresh = refresh
        self.status = iter([dirty_before, dirty_after])

    def run(self, arguments, capture=False):
        self.commands.append(arguments[0] if arguments[0] != "publish" else " ".join(arguments[:2]))
        if capture:
            return 0, self.refresh
        return self.codes.get(arguments[0], 0)

    def git(self, directory, *arguments):
        self.git_calls.append(arguments[0])
        if arguments[0] == "status":
            return f" M {BASELINES}" if next(self.status) else ""
        return ""


def follow(recorder, start=None):
    said = []
    code = run_follow_through(Path("/home"), "2.9.35", follow_through_steps(3, 12), start=start,
                              run=recorder.run, git=recorder.git, say=said.append)
    return code, said


class FollowThroughTest(unittest.TestCase):

    def test_every_step_runs_in_the_order_the_aftermath_needs(self):
        recorder = Recorder()
        code, said = follow(recorder)
        self.assertEqual(0, code)
        self.assertEqual(["git", "publish components", "publish baselines", "check", "build",
                          "native", "build"], recorder.commands)
        self.assertEqual(["status", "status", "commit", "push"], recorder.git_calls)
        self.assertEqual("Follow-through for 2.9.35 is complete", said[-1])

    def test_the_reactor_takes_the_jobs_and_workers_given(self):
        frontends = follow_through_steps(3, 12)[-1]
        self.assertEqual(("build", "--jobs", "3", "--workers", "12", "frontends"), frontends.arguments)
        self.assertEqual(("build", "frontends"), follow_through_steps()[-1].arguments)

    def test_a_failed_step_stops_the_rest_and_names_where_to_resume(self):
        recorder = Recorder(codes={"native": 1})
        code, said = follow(recorder)
        self.assertEqual(1, code)
        self.assertEqual("native", recorder.commands[-1])
        self.assertIn("cedarcli release follow-through --from services", said[-1])

    def test_stale_contracts_are_reported_without_stopping(self):
        recorder = Recorder(codes={"check": 1})
        code, said = follow(recorder)
        self.assertEqual(0, code)
        self.assertTrue(any("contracts reported findings" in line for line in said))

    def test_resuming_starts_at_the_named_step(self):
        recorder = Recorder()
        self.assertEqual(0, follow(recorder, start="java")[0])
        self.assertEqual(["build", "native", "build"], recorder.commands)
        with self.assertRaisesRegex(ReleaseError, "no follow-through step is called 'nope'"):
            follow(Recorder(), start="nope")


class BaselineStepTest(unittest.TestCase):

    def test_someone_else_s_edit_to_the_baselines_is_refused_before_the_refresh(self):
        recorder = Recorder(dirty_before=True)
        code, said = follow(recorder, start="baselines")
        self.assertEqual(1, code)
        self.assertNotIn("publish baselines", recorder.commands)
        self.assertTrue(any("not this command's" in line for line in said))

    def test_a_moved_advisory_count_stops_before_the_commit(self):
        moved = REFRESHED.replace("counts 0/0/0/0 -> 0/0/0/0", "counts 0/0/0/0 -> 0/1/0/0")
        recorder = Recorder(refresh=moved)
        code, said = follow(recorder, start="baselines")
        self.assertEqual(1, code)
        self.assertNotIn("commit", recorder.git_calls)
        self.assertTrue(any("cedar-workspace:package-lock.json 0/0/0/0 -> 0/1/0/0" in line
                            for line in said))

    def test_nothing_is_committed_when_no_baseline_changed(self):
        recorder = Recorder(dirty_after=False)
        self.assertEqual(0, follow(recorder, start="baselines")[0])
        self.assertNotIn("commit", recorder.git_calls)

    def test_only_moved_counts_are_reported(self):
        self.assertEqual([], baseline_count_changes(REFRESHED))


class FollowThroughCommandTest(unittest.TestCase):

    def test_the_dry_run_lists_the_steps_and_runs_none(self):
        with patch.object(release_train, "run_follow_through") as run:
            result = CliRunner().invoke(release_train.app, ["follow-through", "--dry-run"])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("1. pull:", result.output)
        self.assertIn("7. frontends:", result.output)
        run.assert_not_called()

    def test_a_release_that_is_not_accepted_is_refused(self):
        class State:
            def read_current(self):
                return {"conclusion": "abandoned"}

            def read_current_manifest(self):
                return {"releaseVersion": "2.9.35"}, Path("/x")

        with patch.object(release_train, "ReleaseState", State), \
             patch.object(release_train, "run_follow_through") as run:
            result = CliRunner().invoke(release_train.app, ["follow-through"],
                                        env={"CEDAR_HOME": "/tmp"})
        self.assertEqual(1, result.exit_code)
        self.assertIn("not accepted", result.output)
        run.assert_not_called()

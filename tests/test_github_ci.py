import json
import unittest
import subprocess
import sys
from pathlib import Path

from org.metadatacenter.github_ci import (
    GithubCIProbeError,
    finished_runs,
    latest_runs_by_name,
    local_ci_passed,
    newest_commit_status,
    probe_exact_commit,
    unanswered_workflows,
)


class Result:
    def __init__(self, *, payload=None, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = json.dumps(payload) if payload is not None else ""
        self.stderr = stderr


class GithubCIProbeTest(unittest.TestCase):
    def test_captured_policy_imports_without_the_cli_package_on_sys_path(self):
        path = Path(__file__).resolve().parents[1] / 'org/metadatacenter/github_ci.py'
        result = subprocess.run([sys.executable, '-I', '-c', "import importlib.util,sys\nspec=importlib.util.spec_from_file_location('captured_github_ci',sys.argv[1])\nmodule=importlib.util.module_from_spec(spec)\nsys.modules[spec.name]=module\nspec.loader.exec_module(module)\nassert callable(module.probe_exact_commit)\nassert 'org.metadatacenter' not in sys.modules\n", str(path)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_empty_result_is_retried_until_the_commit_is_indexed(self):
        outcomes = [
            Result(payload={"workflow_runs": []}),
            Result(payload={"workflow_runs": [{
                "name": "CI", "status": "completed", "conclusion": "success",
            }]}),
        ]
        slept = []

        probe = probe_exact_commit(
            "repo", "a" * 40, runner=lambda *_args, **_kwargs: outcomes.pop(0),
            sleeper=slept.append, delays=(3,),
        )

        self.assertEqual(2, probe.attempts)
        self.assertEqual([3], slept)

    def test_transient_502_is_retried(self):
        outcomes = [
            Result(returncode=1, stderr="server returned 502 Bad Gateway"),
            Result(payload={"workflow_runs": [{
                "name": "CI", "status": "completed", "conclusion": "success",
            }]}),
        ]
        slept = []

        probe = probe_exact_commit(
            "repo", "a" * 40, runner=lambda *_args, **_kwargs: outcomes.pop(0),
            sleeper=slept.append, delays=(2,),
        )

        self.assertEqual(2, probe.attempts)
        self.assertEqual([2], slept)

    def test_persistent_empty_result_has_a_bounded_verdict(self):
        calls = []

        probe = probe_exact_commit(
            "repo", "a" * 40,
            runner=lambda *_args, **_kwargs: calls.append(1) or Result(
                payload={"workflow_runs": []}),
            sleeper=lambda _delay: None, delays=(0, 0),
        )

        self.assertEqual((), probe.runs)
        self.assertEqual(3, len(calls))

    def test_authentication_refusal_is_not_retried(self):
        calls = []
        with self.assertRaisesRegex(GithubCIProbeError, "HTTP 401"):
            probe_exact_commit(
                "repo", "a" * 40,
                runner=lambda *_args, **_kwargs: calls.append(1) or Result(
                    returncode=1, stderr="gh: HTTP 401: Requires authentication"),
                sleeper=lambda _delay: self.fail("401 must not sleep"), delays=(0, 0),
            )
        self.assertEqual(1, len(calls))

    def test_malformed_response_is_not_retried(self):
        with self.assertRaisesRegex(GithubCIProbeError, "malformed JSON"):
            probe_exact_commit(
                "repo", "a" * 40,
                runner=lambda *_args, **_kwargs: Result(),
                sleeper=lambda _delay: self.fail("malformed JSON must not sleep"),
            )


def _run(name, conclusion, status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


class WorkflowAnswerTest(unittest.TestCase):
    def test_a_workflow_whose_every_run_was_cancelled_is_unanswered_beside_one_that_finished(self):
        runs = [_run("Test", "cancelled"), _run("Lint", "success")]

        self.assertEqual(["Test"], unanswered_workflows(runs))
        self.assertEqual(["Lint"], list(latest_runs_by_name(finished_runs(runs))))

    def test_a_finished_run_of_the_same_workflow_answers_for_a_cancelled_one(self):
        runs = [_run("Test", "cancelled"), _run("Test", "success"), _run("Lint", "success")]

        self.assertEqual([], unanswered_workflows(runs))

    def test_a_run_still_in_progress_is_an_answer_to_wait_for(self):
        runs = [_run("Test", "cancelled"), _run("Test", None, status="in_progress")]

        self.assertEqual([], unanswered_workflows(runs))

    def test_every_cancelled_workflow_is_named_once_in_the_order_github_lists_them(self):
        runs = [_run("Test", "cancelled"), _run("Lint", "cancelled"), _run("Test", "cancelled")]

        self.assertEqual(["Test", "Lint"], unanswered_workflows(runs))


class CommitStatusTest(unittest.TestCase):
    def _statuses(self, *statuses):
        return lambda *_args, **_kwargs: Result(payload=list(statuses))

    def test_the_newest_status_of_the_context_answers(self):
        runner = self._statuses(
            {"context": "cedar/local-ci", "state": "failure", "updated_at": "2026-11-10T09:00:00Z"},
            {"context": "cedar/local-ci", "state": "success", "updated_at": "2026-11-10T10:00:00Z"},
            {"context": "other", "state": "failure", "updated_at": "2026-11-10T11:00:00Z"},
        )

        newest, reason = newest_commit_status("repo", "a" * 40, "cedar/local-ci", runner=runner)

        self.assertEqual(("success", ""), (newest["state"], reason))
        self.assertTrue(local_ci_passed("repo", "a" * 40, runner=runner))

    def test_a_later_failure_withdraws_an_earlier_pass(self):
        runner = self._statuses(
            {"context": "cedar/local-ci", "state": "success", "updated_at": "2026-11-10T09:00:00Z"},
            {"context": "cedar/local-ci", "state": "failure", "updated_at": "2026-11-10T10:00:00Z"},
        )

        self.assertFalse(local_ci_passed("repo", "a" * 40, runner=runner))

    def test_no_status_and_an_unreadable_one_both_say_why(self):
        absent, reason = newest_commit_status(
            "repo", "a" * 40, "cedar/local-ci", runner=self._statuses())
        self.assertIsNone(absent)
        self.assertEqual("no cedar/local-ci status on aaaaaaaa", reason)

        refused = lambda *_args, **_kwargs: Result(returncode=1, stderr="HTTP 403")
        unreadable, reason = newest_commit_status("repo", "a" * 40, "cedar/local-ci", runner=refused)
        self.assertIsNone(unreadable)
        self.assertIn("unreadable: HTTP 403", reason)
        self.assertFalse(local_ci_passed("repo", "a" * 40, runner=refused))


if __name__ == "__main__":
    unittest.main()

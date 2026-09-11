import json
import unittest
import subprocess
import sys
from pathlib import Path

from org.metadatacenter.github_ci import GithubCIProbeError, probe_exact_commit


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


if __name__ == "__main__":
    unittest.main()

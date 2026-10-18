import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from typer.testing import CliRunner
from org.metadatacenter import search_harness, test_processes


class SearchHarnessTest(unittest.TestCase):
    @patch.object(search_harness, "run_search", return_value=0)
    def test_cli_default_is_offline_and_conflicting_modes_are_forwarded_for_validation(self, run):
        result = CliRunner().invoke(test_processes.app, ["search"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(run.call_args.kwargs["live"])
        result = CliRunner().invoke(test_processes.app, ["search", "--check"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertFalse(run.call_args.kwargs["live"])

    @patch.object(search_harness.subprocess, "run", return_value=Mock(returncode=0))
    @patch.object(search_harness, "invocation_environment", return_value={"CEDAR_CLI_CALLER_DIR": "/tmp/caller"})
    def test_default_and_each_offline_mode_never_include_live(self, environment, run):
        with patch.object(search_harness.Util, "cedar_home", "/tmp/cedar"):
            for kwargs in ({}, {"plan": True}, {"check": True}, {"validate": True}):
                self.assertEqual(search_harness.run_search(**kwargs), 0)
                self.assertNotIn("--live", run.call_args.args[0])
                self.assertEqual(run.call_args.kwargs["cwd"], Path("/tmp/cedar/cedar-development/ops/e2e").resolve())

    @patch.object(search_harness.subprocess, "run")
    def test_invalid_or_ambiguous_modes_spawn_nothing(self, run):
        for kwargs in ({"live": True, "check": True}, {"live": True, "validate": True},
                       {"live": True, "plan": True}, {"check": True, "validate": True}, {"tier": "invalid"}):
            with self.assertRaises(ValueError):
                search_harness.run_search(**kwargs)
        run.assert_not_called()

    @patch.object(search_harness.subprocess, "run", return_value=Mock(returncode=7))
    @patch.object(search_harness, "invocation_environment", return_value={"CEDAR_CLI_CALLER_DIR": "/tmp/caller"})
    def test_live_requires_explicit_mode_and_keeps_failure_exit_and_report_location(self, environment, run):
        with patch.object(search_harness.Util, "cedar_home", "/tmp/cedar"):
            self.assertEqual(search_harness.run_search(live=True, tier="all", report="evidence.json"), 7)
        args = run.call_args.args[0]
        self.assertIn("--live", args)
        self.assertIn("--tier=all", args)
        self.assertIn(f"--report={Path('/tmp/caller/evidence.json').resolve()}", args)


if __name__ == "__main__":
    unittest.main()

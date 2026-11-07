import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from typer.testing import CliRunner
from org.metadatacenter import frontend_performance as perf, test_processes


class FrontendPerformanceTest(unittest.TestCase):
    @patch.object(perf, "run_performance", return_value=0)
    def test_explicit_command_defaults_and_plan(self, run):
        result = CliRunner().invoke(test_processes.app, ["frontend-performance", "--plan"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(run.call_args.kwargs["plan"])
        self.assertEqual(run.call_args.kwargs["samples"], 5)

    @patch.object(perf.subprocess, "run", return_value=Mock(returncode=1))
    @patch.object(perf, "invocation_environment", return_value={"CEDAR_CLI_CALLER_DIR": "/tmp/caller"})
    @patch.object(perf, "require_build_node")
    def test_reports_resolve_from_caller_and_functional_failures_propagate(self, node, environment, run):
        with patch.object(perf.Util, "cedar_home", "/tmp/cedar"):
            self.assertEqual(perf.run_performance(output="measurements", scenarios="ced-standalone-large"), 1)
        command = run.call_args.args[0]
        self.assertIn("--scenarios=ced-standalone-large", command)
        self.assertIn(f"--output={Path('/tmp/caller/measurements').resolve()}", command)
        self.assertNotIn("--skip-tests", command)

    @patch.object(perf.subprocess, "run")
    def test_invalid_settings_do_not_launch_work(self, run):
        for kwargs in ({"plan":True,"check":True}, {"samples":0}, {"samples":True}, {"profiles":"unknown"}):
            with self.assertRaises(ValueError): perf.run_performance(**kwargs)
        run.assert_not_called()

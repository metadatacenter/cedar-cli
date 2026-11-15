import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from typer.testing import CliRunner
from org.metadatacenter import ui_review, test_processes


class UIReviewTest(unittest.TestCase):
    @patch.object(ui_review.subprocess, 'run', return_value=Mock(returncode=0))
    @patch.object(ui_review, 'runtime_evidence')
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_sealing_an_existing_review_does_not_recapture_or_require_current_sources(self, environment, evidence, run):
        with patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            self.assertEqual(ui_review.run_review(seal_review=True, output='/tmp/review'), 0)
            with self.assertRaises(ValueError):
                ui_review.run_review(seal_review=True)
        self.assertIn('--seal-review', run.call_args.args[0])
        evidence.assert_not_called()

    @patch.object(ui_review, 'run_review', return_value=2)
    def test_cli_keeps_incomplete_exit_status(self, run):
        result = CliRunner().invoke(test_processes.app, ['ui-review', '--plan'])
        self.assertEqual(result.exit_code, 2)
        self.assertTrue(run.call_args.kwargs['plan'])

    @patch.object(ui_review.subprocess, 'run')
    @patch.object(ui_review, 'runtime_evidence', return_value={'current': False, 'stale': ['cedar-embeddable-designer']})
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_stale_runtime_cannot_start_an_acceptance_capture(self, environment, evidence, run):
        with patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            with self.assertRaisesRegex(ValueError, 'Frontend sources changed'):
                ui_review.run_review()
        run.assert_not_called()

    @patch.object(ui_review.subprocess, 'run', return_value=Mock(returncode=0))
    @patch.object(ui_review, 'runtime_evidence', return_value={'current': False, 'stale': ['cedar-embeddable-designer']})
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_diagnostic_retains_staleness_and_refuses_overwriting_evidence(self, environment, evidence, run):
        with tempfile.TemporaryDirectory() as parent, patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            directory = Path(parent) / 'run'
            self.assertEqual(ui_review.run_review(diagnostic=True, output=directory), 0)
            self.assertFalse(json.loads((directory / 'provenance.json').read_text())['current'])
            with self.assertRaises(FileExistsError):
                ui_review.run_review(diagnostic=True, output=directory)
        self.assertEqual(run.call_count, 1)

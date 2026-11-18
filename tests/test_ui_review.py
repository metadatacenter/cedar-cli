import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from typer.testing import CliRunner
from org.metadatacenter import ui_review, test_processes


APPS = ['designer', 'workspace', 'metadata-editor']
CATALOG_APPLICATIONS = ui_review.applications


class UIReviewTest(unittest.TestCase):
    def setUp(self):
        catalog = patch.object(ui_review, 'applications', return_value=APPS)
        self.applications = catalog.start()
        self.addCleanup(catalog.stop)

    @patch.object(ui_review, '_invoke', return_value=0)
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

    @patch.object(ui_review, '_invoke')
    @patch.object(ui_review, 'runtime_evidence', return_value={'current': False, 'stale': ['cedar-embeddable-designer']})
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_stale_runtime_cannot_start_an_acceptance_capture(self, environment, evidence, run):
        with patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            with self.assertRaisesRegex(ValueError, 'Frontend sources changed'):
                ui_review.run_review()
        run.assert_not_called()

    @patch.object(ui_review, '_invoke', return_value=0)
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

    @patch.object(ui_review, 'run_review', return_value=0)
    def test_cli_forwards_application_selector(self, run):
        result = CliRunner().invoke(test_processes.app, ['ui-review', '--app', 'all', '--plan'])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(run.call_args.kwargs['app'], 'all')

    @patch.object(ui_review, '_invoke', return_value=0)
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_plan_forwards_app_without_capturing(self, environment, run):
        with patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            ui_review.run_review(app='metadata-editor', plan=True)
            self.assertIn('--app=metadata-editor', run.call_args.args[0])
            with self.assertRaisesRegex(ValueError, 'Unknown UI review'):
                ui_review.run_review(app='typo', plan=True)

    @patch.object(ui_review, 'source_identity')
    def test_freshness_uses_content_and_reports_metadata_only_changes(self, identity):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / '.reactor/builds').mkdir(parents=True)
            (home / '.reactor/runtime.json').write_text(json.dumps({'build': 'abc', 'packages': {}}))
            recorded = {'sourceSha256': 'same-bytes', 'revision': 'before', 'dirty': True}
            (home / '.reactor/builds/abc.json').write_text(json.dumps({'repositories': {'app': {'source': recorded}}}))
            identity.return_value = {'sourceSha256': 'same-bytes', 'revision': 'committed', 'dirty': False}
            evidence = ui_review.runtime_evidence(home)
            self.assertTrue(evidence['current'])
            self.assertEqual(evidence['metadataOnlyChanges'], ['app'])
            identity.return_value = {'sourceSha256': 'different-bytes'}
            evidence = ui_review.runtime_evidence(home)
            self.assertFalse(evidence['current'])
            self.assertEqual(evidence['stale'], ['app'])
            identity.return_value = {'revision': 'unknown'}
            self.assertFalse(ui_review.runtime_evidence(home)['current'])

    @patch.object(ui_review.subprocess, 'Popen')
    def test_interrupt_allows_cleanup_before_returning(self, popen):
        process = popen.return_value
        process.wait.side_effect = [KeyboardInterrupt, 143]
        self.assertEqual(ui_review._invoke(['node', 'collector'], cwd='/tmp', env={}), 130)
        process.send_signal.assert_called_once_with(ui_review.signal.SIGTERM)
        process.kill.assert_not_called()
        self.assertEqual(process.wait.call_args.kwargs, {'timeout': 60})

    @patch.object(ui_review.subprocess, 'run')
    def test_application_list_comes_from_the_review_catalog(self, run):
        run.return_value = Mock(stdout='["designer", "workspace", "metadata-editor", "openview"]\n')
        self.assertEqual(CATALOG_APPLICATIONS('/tmp/e2e', {'A': 'B'})[-1], 'openview')
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ['node', '--input-type=module', '-e'])
        self.assertIn("./ui-review/catalog.mjs", command[3])
        self.assertEqual(run.call_args.kwargs['cwd'], '/tmp/e2e')
        self.assertTrue(run.call_args.kwargs['check'])

    @patch.object(ui_review, '_invoke', return_value=0)
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_an_application_the_catalog_adds_needs_no_cli_change(self, environment, run):
        self.applications.return_value = APPS + ['openview']
        with patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            self.assertEqual(ui_review.run_review(app='openview', plan=True), 0)
        self.assertIn('--app=openview', run.call_args.args[0])

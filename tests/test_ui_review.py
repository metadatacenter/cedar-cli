import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from typer.testing import CliRunner
from org.metadatacenter import ui_review, test_processes


APPS = ['designer', 'workspace', 'metadata-editor']
PROFILES = ['chromium', 'webkit-touch']
CATALOG = ui_review.catalog


class UIReviewTest(unittest.TestCase):
    def setUp(self):
        known = patch.object(ui_review, 'catalog', return_value={'applications': APPS, 'profiles': PROFILES})
        self.catalog = known.start()
        self.addCleanup(known.stop)

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
            self.assertIn('--profile=chromium', run.call_args.args[0])
            # The touch profile's catalog differs, so planning must name it rather than fall back.
            ui_review.run_review(app='designer', plan=True, profile='webkit-touch')
            self.assertIn('--profile=webkit-touch', run.call_args.args[0])
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
    def test_applications_and_profiles_come_from_the_review_catalog(self, run):
        run.return_value = Mock(stdout='{"applications": ["designer", "openview"], "profiles": ["chromium", "webkit-touch"]}\n')
        self.assertEqual(CATALOG('/tmp/e2e', {'A': 'B'}), {'applications': ['designer', 'openview'], 'profiles': ['chromium', 'webkit-touch']})
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ['node', '--input-type=module', '-e'])
        self.assertIn("./ui-review/catalog.mjs", command[3])
        self.assertEqual(run.call_args.kwargs['cwd'], '/tmp/e2e')
        self.assertTrue(run.call_args.kwargs['check'])

    @patch.object(ui_review, '_invoke', return_value=0)
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_an_application_the_catalog_adds_needs_no_cli_change(self, environment, run):
        self.catalog.return_value = {'applications': APPS + ['openview'], 'profiles': PROFILES}
        with patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            self.assertEqual(ui_review.run_review(app='openview', plan=True), 0)
        self.assertIn('--app=openview', run.call_args.args[0])

    @patch.object(ui_review, '_invoke', return_value=0)
    @patch.object(ui_review, 'served_findings', return_value=['the designer serves stale components'])
    @patch.object(ui_review, 'runtime_evidence', return_value={'build': 'b', 'current': True, 'stale': []})
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_frontends_serving_another_build_refuse_acceptance_and_are_recorded_in_a_diagnostic(
            self, environment, evidence, served, run):
        with tempfile.TemporaryDirectory() as parent, patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            with self.assertRaisesRegex(ValueError, 'do not serve the selected build: the designer serves stale'):
                ui_review.run_review(output=Path(parent) / 'refused')
            self.assertFalse((Path(parent) / 'refused').exists())
            self.assertEqual(ui_review.run_review(diagnostic=True, output=Path(parent) / 'diagnostic'), 0)
            provenance = json.loads((Path(parent) / 'diagnostic/provenance.json').read_text())
        self.assertFalse(provenance['current'])
        self.assertEqual(provenance['servedFindings'], ['the designer serves stale components'])
        self.assertEqual(run.call_count, 1)

    @patch.object(ui_review, '_invoke', return_value=0)
    @patch.object(ui_review, 'served_findings', return_value=[])
    @patch.object(ui_review, 'runtime_evidence', return_value={'current': True, 'stale': []})
    @patch.object(ui_review, 'invocation_environment', return_value={})
    def test_capture_forwards_a_catalog_profile_and_refuses_others_before_writing(self, environment, evidence, served, run):
        with tempfile.TemporaryDirectory() as parent, patch.object(ui_review.Util, 'cedar_home', '/tmp/cedar'):
            with self.assertRaisesRegex(ValueError, 'Unknown UI review browser profile'):
                ui_review.run_review(output=Path(parent) / 'refused', profile='firefox-touch')
            self.assertFalse((Path(parent) / 'refused').exists())
            self.assertEqual(ui_review.run_review(output=Path(parent) / 'touch', profile='webkit-touch'), 0)
        self.assertIn('--profile=webkit-touch', run.call_args.args[0])


class CaptureAfterBuildTest(unittest.TestCase):
    def evidence(self, designer='a', other='x'):
        return {'build': 'b' * 64, 'sources': {
            'cedar-embeddable-designer': {'current': {'sourceSha256': designer}},
            'cedar-monitoring/cedar-monitoring-src': {'current': {'sourceSha256': other}}}}

    def test_captures_when_a_reviewed_source_changed_and_records_only_complete_captures(self):
        with tempfile.TemporaryDirectory() as home, \
                patch.object(ui_review, 'runtime_evidence', return_value=self.evidence()), \
                patch.object(ui_review, 'run_review', side_effect=[2, 0]) as run:
            printed = []
            self.assertEqual(ui_review.capture_after_build(home, printer=printed.append), 2)
            latest = Path(home) / '.cedar/reports/ui-review/automatic/latest.json'
            self.assertFalse(latest.exists())
            self.assertIn('INCOMPLETE', printed[-1])
            self.assertEqual(ui_review.capture_after_build(home, printer=printed.append), 0)
            self.assertEqual(json.loads(latest.read_text())['inputs']['sources'], {'cedar-embeddable-designer': 'a'})
            self.assertEqual(run.call_args.kwargs['app'], 'all')

    def test_an_unreviewed_source_alone_does_not_capture_again(self):
        with tempfile.TemporaryDirectory() as home, patch.object(ui_review, 'run_review', side_effect=self.capture) as run:
            with patch.object(ui_review, 'runtime_evidence', return_value=self.evidence(other='x')):
                ui_review.capture_after_build(home, printer=lambda *_: None)
            with patch.object(ui_review, 'runtime_evidence', return_value=self.evidence(other='y')):
                self.assertIsNone(ui_review.capture_after_build(home, printer=lambda *_: None))
            with patch.object(ui_review, 'runtime_evidence', return_value=self.evidence(designer='changed')):
                ui_review.capture_after_build(home, printer=lambda *_: None)
        self.assertEqual(run.call_count, 2)

    def test_keeps_only_the_most_recent_automatic_packets(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / '.cedar/reports/ui-review/automatic'
            for index in range(7):
                (root / f'2026010{index}T000000Z-old').mkdir(parents=True)
            def capture(**kwargs):
                kwargs['output'].mkdir()
                return 0
            with patch.object(ui_review, 'runtime_evidence', return_value=self.evidence()), \
                    patch.object(ui_review, 'run_review', side_effect=capture):
                ui_review.capture_after_build(home, printer=lambda *_: None)
            kept = sorted(path.name for path in root.iterdir() if path.is_dir())
            self.assertEqual(len(kept), ui_review.AUTOMATIC_PACKETS_KEPT)
            self.assertNotIn('20260100T000000Z-old', kept)

    def capture(self, **kwargs):
        # Captures in one test can fall in the same second, and so name the same directory.
        kwargs['output'].mkdir(exist_ok=True)
        return 0

    def test_a_changed_package_model_library_or_review_tooling_captures_again(self):
        with tempfile.TemporaryDirectory() as home, patch.object(ui_review, 'run_review', side_effect=self.capture) as run:
            for evidence, tooling in [(self.evidence(), 't'), ({**self.evidence(), 'packages': {'cedar-embeddable-editor': 'p2'}}, 't'),
                                      ({**self.evidence(), 'packages': {'cedar-embeddable-editor': 'p2'}}, 'u')]:
                with patch.object(ui_review, 'runtime_evidence', return_value=evidence), \
                        patch.object(ui_review, 'review_tooling_sha256', return_value=tooling):
                    self.assertEqual(ui_review.capture_after_build(home, printer=lambda *_: None), 0)
            model = self.evidence()
            model['sources']['cedar-model-typescript-library'] = {'current': {'sourceSha256': 'm2'}}
            model['packages'] = {'cedar-embeddable-editor': 'p2'}
            with patch.object(ui_review, 'runtime_evidence', return_value=model), \
                    patch.object(ui_review, 'review_tooling_sha256', return_value='u'):
                self.assertEqual(ui_review.capture_after_build(home, printer=lambda *_: None), 0)
                self.assertIsNone(ui_review.capture_after_build(home, printer=lambda *_: None))
        self.assertEqual(run.call_count, 4)

    def test_an_unreadable_record_or_evidence_is_reported_and_never_raises(self):
        with tempfile.TemporaryDirectory() as home:
            latest = Path(home) / '.cedar/reports/ui-review/automatic/latest.json'
            latest.parent.mkdir(parents=True)
            latest.write_text('{not json')
            with patch.object(ui_review, 'runtime_evidence', return_value=self.evidence()), \
                    patch.object(ui_review, 'run_review', side_effect=self.capture):
                self.assertEqual(ui_review.capture_after_build(home, printer=lambda *_: None), 0)
            printed = []
            with patch.object(ui_review, 'runtime_evidence', side_effect=KeyError('build')):
                self.assertEqual(ui_review.capture_after_build(home, printer=printed.append), 2)
        self.assertIn('build is unaffected', printed[-1])

    def test_incomplete_captures_never_evict_the_complete_packet_the_record_names(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / '.cedar/reports/ui-review/automatic'
            with patch.object(ui_review, 'runtime_evidence', return_value=self.evidence()), \
                    patch.object(ui_review, 'run_review', side_effect=self.capture):
                ui_review.capture_after_build(home, printer=lambda *_: None)
            complete = Path(json.loads((root / 'latest.json').read_text())['packet'])
            def incomplete(**kwargs):
                kwargs['output'].mkdir()
                return 2
            for index in range(ui_review.AUTOMATIC_PACKETS_KEPT + 2):
                with patch.object(ui_review, 'runtime_evidence', return_value=self.evidence(designer=f'd{index}')), \
                        patch.object(ui_review, 'run_review', side_effect=incomplete), \
                        patch.object(ui_review.time, 'strftime', return_value=f'20990101T00000{index}Z'):
                    ui_review.capture_after_build(home, printer=lambda *_: None)
            self.assertTrue(complete.is_dir())
            # A record whose packet is gone does not skip the capture its sources call for.
            ui_review.shutil.rmtree(complete)
            with patch.object(ui_review, 'runtime_evidence', return_value=self.evidence()), \
                    patch.object(ui_review, 'run_review', side_effect=self.capture) as run:
                self.assertEqual(ui_review.capture_after_build(home, printer=lambda *_: None), 0)
            run.assert_called_once()

    def test_a_capture_that_cannot_start_is_reported_without_raising(self):
        with tempfile.TemporaryDirectory() as home, \
                patch.object(ui_review, 'runtime_evidence', return_value=self.evidence()), \
                patch.object(ui_review, 'run_review', side_effect=ValueError('Frontend sources changed')):
            printed = []
            self.assertEqual(ui_review.capture_after_build(home, printer=printed.append), 2)
        self.assertTrue(any('could not run' in line for line in printed))

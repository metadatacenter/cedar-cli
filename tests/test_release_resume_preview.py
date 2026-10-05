import copy
import unittest
from unittest.mock import patch
from typer.testing import CliRunner
from rich.console import Console
from org.metadatacenter.release_resume_preview import render_resume_preview, resume_preview
from org.metadatacenter.release_support.preflight import ReleasePreflight
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter import release_train

class ResumePreviewTest(unittest.TestCase):
    def test_partial_build_preserves_completed_tasks_and_lists_real_checks(self):
        manifest = {'phase':'build-validation-failed', 'buildValidation': {'completedTasks': {'release:maven:parent':{}}}}
        before = copy.deepcopy(manifest)
        report = resume_preview(manifest)
        self.assertEqual(manifest, before)
        self.assertEqual(ReleasePreflight.resume_check_names(manifest), report['checks'])
        build = next(row for row in report['stages'] if row['stage'] == 'builds')
        self.assertEqual(['release:maven:parent'], build['recordedTasks'])
        self.assertTrue(build['pending'])
        self.assertFalse(report['stages'][0]['pending'])

    def test_preview_names_the_acceptances_resume_applies_again(self):
        manifest = {'releaseVersion': '1.0.0', 'phase': 'build-validation-failed',
                    'acceptances': {'redDevelop': {'cedar-repo-server': '42'}, 'mainOnly': ['cedar-workspace']}}
        console = Console(record=True, width=200)
        render_resume_preview(manifest, console)
        output = console.export_text()
        self.assertIn('red develop cedar-repo-server=42', output)
        self.assertIn('main-only cedar-workspace', output)

    def test_version_failure_rewinds_and_terminal_cases_are_honest(self):
        report = resume_preview({'phase':'version-preparation-failed'})
        self.assertIn('not reused', report['note'])
        self.assertTrue(report['stages'][0]['pending'])
        self.assertEqual([], resume_preview({'phase':'accepted'})['checks'])
        with self.assertRaises(ReleaseError):
            resume_preview({'phase':'abandoned'})

    def test_preview_cli_never_locks_activates_checks_or_drives(self):
        with patch.object(release_train, 'ReleaseState') as state, \
             patch.object(release_train, '_activate_toolchain') as activate, \
             patch.object(release_train, '_release_resume_gate_or_exit') as gate, \
             patch.object(release_train, '_drive_release') as drive:
            state.return_value.read_current_manifest.return_value = ({'releaseVersion':'1.0.0','phase':'started'}, '/state')
            result = CliRunner().invoke(release_train.app, ['resume','--dry-run'])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertIn('No checks executed', result.output)
            state.return_value.exclusive.assert_not_called()
            activate.assert_not_called(); gate.assert_not_called(); drive.assert_not_called()

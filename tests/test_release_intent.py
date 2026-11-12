import unittest
from types import SimpleNamespace
from unittest.mock import patch
from org.metadatacenter.train_support import dispatch, release_intent
from org.metadatacenter.release_support.preflight import PreflightFinding


class ReleaseIntentTest(unittest.TestCase):
    def test_inputs_are_all_or_none_and_versions_must_advance(self):
        self.assertIsNone(release_intent.validate_intent(None, None, None))
        for values in [('2.9.19', None, None), ('2.9.19','2.9.19-SNAPSHOT','2.0.17'),
                       ('2.9.19','garbage','2.0.17'), ('2.9.19','2.9.20-SNAPSHOT','dev')]:
            with self.assertRaises(ValueError):
                release_intent.validate_intent(*values)
        with self.assertRaisesRegex(ValueError, 'does not target'):
            release_intent.validate_intent('2.9.19','2.9.20-SNAPSHOT','2.0.17','2.9.18-dev.20260923.0622')

    def test_failed_release_prerequisite_never_dispatches_even_without_dry_run(self):
        with patch.object(dispatch.BuildTrain,'allocate', return_value='2.9.19-dev.20260923.0622'), \
             patch.object(dispatch,'_activate_release_toolchain'), \
             patch.object(release_intent,'preflight',side_effect=ValueError('Nexus unavailable')), \
             patch.object(dispatch.subprocess,'run') as run:
            self.assertEqual(1,dispatch.dispatch(release_version='2.9.19',
                next_version='2.9.20-SNAPSHOT',cee_version='2.0.17'))
            run.assert_not_called()

    def test_unreadable_target_refs_fail_closed(self):
        import tempfile
        from pathlib import Path
        from org.metadatacenter.release_support.preflight import ReleasePreflight
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'repo').mkdir()
            checker = ReleasePreflight({'releaseVersion':'2.9.19',
                'nextDevelopmentVersion':'2.9.20-SNAPSHOT', 'releaseRepositories':['repo']},
                environment={'CEDAR_HOME':directory})
            with patch.object(checker, '_capture', return_value=(128,'','remote unavailable')):
                findings = checker.check_target_version_unused()
            self.assertEqual(1,len(findings))
            self.assertTrue(findings[0].fatal)
            self.assertIn('cannot verify unused',findings[0].message)

    def test_partial_intent_fails_before_train_allocation(self):
        with patch.object(dispatch.BuildTrain,'allocate') as allocate:
            self.assertEqual(1,dispatch.dispatch(release_version='2.9.19'))
            allocate.assert_not_called()

    def test_main_only_acceptance_requires_release_intent(self):
        with patch.object(dispatch.BuildTrain, 'allocate') as allocate:
            self.assertEqual(1, dispatch.dispatch(accept_main_only=['example']))
            allocate.assert_not_called()

    def test_dry_run_checks_release_readiness_without_dispatching(self):
        with patch.object(dispatch.BuildTrain,'allocate', return_value='2.9.19-dev.20260923.0622'), \
             patch.object(dispatch,'_activate_release_toolchain'), \
             patch.object(release_intent,'preflight',return_value=[]) as check, \
             patch.object(dispatch,'_dry_run',return_value=0) as dry, \
             patch.object(dispatch.subprocess,'run') as run:
            self.assertEqual(0,dispatch.dispatch(dry_run=True,release_version='2.9.19',
                next_version='2.9.20-SNAPSHOT',cee_version='2.0.17', accept_main_only=['example']))
            check.assert_called_once()
            self.assertEqual({'example'}, check.call_args.kwargs['accepted_main_only'])
            dry.assert_called_once()
            run.assert_not_called()

    def test_release_prerequisites_run_with_the_release_toolchain(self):
        # release plan and start put Java 17 and Node 24 first on PATH; the train's release check
        # must judge the same toolchain, or it refuses a shell the release itself accepts.
        order = []
        with patch.object(dispatch.BuildTrain,'allocate', return_value='2.9.19-dev.20260923.0622'), \
             patch.object(dispatch,'_activate_release_toolchain',
                          side_effect=lambda: order.append('toolchain')), \
             patch.object(release_intent,'preflight',
                          side_effect=lambda *a, **k: order.append('preflight') or []), \
             patch.object(dispatch,'_dry_run',return_value=0):
            self.assertEqual(0,dispatch.dispatch(dry_run=True,release_version='2.9.19',
                next_version='2.9.20-SNAPSHOT',cee_version='2.0.17'))
        self.assertEqual(['toolchain','preflight'], order)

    def test_activating_the_toolchain_reports_each_substitution(self):
        with patch('org.metadatacenter.release_support.toolchain.ToolchainResolver.resolve',
                   return_value=['Node v24.22.1 from /opt/homebrew/opt/node@24/bin']), \
             patch.object(dispatch._output_component.console,'print') as printed:
            dispatch._activate_release_toolchain()
        printed.assert_called_once_with(
            'Toolchain: Node v24.22.1 from /opt/homebrew/opt/node@24/bin', markup=False)

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

    def test_dry_run_checks_release_readiness_without_dispatching(self):
        with patch.object(dispatch.BuildTrain,'allocate', return_value='2.9.19-dev.20260923.0622'), \
             patch.object(release_intent,'preflight',return_value=[]) as check, \
             patch.object(dispatch,'_dry_run',return_value=0) as dry, \
             patch.object(dispatch.subprocess,'run') as run:
            self.assertEqual(0,dispatch.dispatch(dry_run=True,release_version='2.9.19',
                next_version='2.9.20-SNAPSHOT',cee_version='2.0.17'))
            check.assert_called_once()
            dry.assert_called_once()
            run.assert_not_called()

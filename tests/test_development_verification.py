import copy
import datetime as dt
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from org.metadatacenter.release_support.development import DevelopmentVerifier, verify_active_development
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.lifecycle import _next_release_stage, release_stages


class MemoryState:
    def __init__(self, manifest): self.manifest = manifest
    def read_current_manifest(self): return copy.deepcopy(self.manifest), None
    def update_current_manifest(self, change):
        self.manifest.update(copy.deepcopy(change))
        return self.read_current_manifest()


class DevelopmentVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = {'phase':'artifacts-published','developmentVerificationPolicy':1,
            'versionPreparation':{'nextDevelopment':{'workspace':str(self.root)}},
            'remoteIntegration':{'completedTasks':{}}}
        self.state = MemoryState(self.manifest)
        self.acceptance = Mock()
        self.acceptance._next_development_can_seed_train.return_value = [{'check':'baselines'}]
        self.runner = Mock(return_value=SimpleNamespace(returncode=0))

    def repository(self, repo):
        path = self.root / repo / '.github/workflows'
        path.mkdir(parents=True)
        (path / 'ci.yml').write_text('name: CI')
        self.manifest['remoteIntegration']['completedTasks'][repo] = {
            'repository':repo,'develop':{'commit':'a'*40}}

    def run_record(self, **changes):
        return dict({'id':123, 'name':'CI', 'head_sha':'a'*40, 'head_branch':'develop',
            'status':'completed', 'conclusion':'success', 'event':'push',
            'path':'.github/workflows/ci.yml','created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'html_url':'https://github.com/metadatacenter/example/actions/runs/123'}, **changes)

    def verifier(self, runs, **kwargs):
        probe = Mock(side_effect=[SimpleNamespace(runs=tuple(items)) for items in runs])
        return DevelopmentVerifier(self.state, acceptance=self.acceptance, runner=self.runner,
            probe=probe, sleeper=Mock(), polls=len(runs), delay=0, environment={}, **kwargs)

    def test_exact_commit_pending_then_success_records_evidence(self):
        self.repository('example')
        verifier = self.verifier([[self.run_record(head_sha='b'*40)], [self.run_record()]])
        result = verify_active_development(self.state, verifier)
        self.assertEqual('development-verified',result['phase'])
        self.assertEqual('a'*40,result['developmentVerification']['repositories']['example']['revision'])
        self.assertEqual(2,self.acceptance._remote_state_still_holds.call_count)
        self.runner.assert_not_called()

    def test_only_pending_repositories_are_polled_again(self):
        self.repository('ready')
        self.repository('waiting')
        verifier = self.verifier([[self.run_record()],
            [self.run_record(status='in_progress',conclusion=None)], [self.run_record()]])
        verify_active_development(self.state,verifier)
        self.assertEqual(['ready','waiting','waiting'],
                         [call.args[0] for call in verifier.probe.call_args_list])

    def test_wall_clock_deadline_prevents_another_poll(self):
        self.repository('example')
        verifier = self.verifier([[]],timeout=1,clock=Mock(side_effect=[0,2]))
        with self.assertRaisesRegex(ReleaseError,'time limit'):
            verify_active_development(self.state,verifier)
        verifier.probe.assert_not_called()

    def test_failed_ci_stops_immediately_and_preserves_failure(self):
        self.repository('example')
        verifier = self.verifier([[self.run_record(conclusion='failure')]])
        with self.assertRaisesRegex(ReleaseError,'Next-development CI failed'):
            verify_active_development(self.state,verifier)
        self.assertEqual('development-verification-failed',self.state.manifest['phase'])
        self.assertNotIn('completedAt',self.state.manifest['developmentVerification'])

    def test_aggregator_dispatch_is_after_readiness_and_not_repeated_on_resume(self):
        self.repository('cedar-project')
        verifier = self.verifier([[self.run_record()]])  # old push run is not proof
        with self.assertRaisesRegex(ReleaseError,'still pending'):
            verify_active_development(self.state,verifier)
        self.runner.assert_called_once()
        self.acceptance._published_artifacts_still_hold.assert_called_once()
        self.acceptance._next_development_can_seed_train.assert_called_once()
        verifier = self.verifier([[self.run_record(event='workflow_dispatch')]])
        verify_active_development(self.state,verifier)
        self.runner.assert_called_once()

    def test_unavailable_snapshots_prevent_dispatch(self):
        self.repository('cedar-project')
        self.acceptance._published_artifacts_still_hold.side_effect=ReleaseError('snapshot missing')
        with self.assertRaisesRegex(ReleaseError,'snapshot missing'):
            verify_active_development(self.state,self.verifier([[]]))
        self.runner.assert_not_called()

    def test_uncertain_dispatch_is_durable_and_reconciles(self):
        self.repository('cedar-project')
        self.runner.return_value.returncode=1
        with self.assertRaisesRegex(ReleaseError,'uncertain outcome'):
            verify_active_development(self.state,self.verifier([[]]))
        self.assertIn('cedar-project',self.state.manifest['developmentVerification']['dispatches'])
        verify_active_development(self.state,self.verifier([[self.run_record(event='workflow_dispatch')]]))
        self.runner.assert_called_once()

    def test_new_stage_is_resumable_and_legacy_ledgers_keep_their_contract(self):
        self.assertEqual('development',_next_release_stage(self.manifest))
        for phase in ('verifying-development','development-verification-failed'):
            self.assertEqual('development',_next_release_stage({**self.manifest,'phase':phase}))
        self.assertEqual('acceptance',_next_release_stage({**self.manifest,'phase':'development-verified'}))
        self.assertEqual('acceptance',_next_release_stage({'phase':'artifacts-published'}))
        stages=release_stages(self.manifest)
        for first,second in zip(stages,stages[1:]):
            self.assertIn(first.done_phase,second.entry_phases)

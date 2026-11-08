import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from org.metadatacenter.util.BuildTrain import BuildTrain, DockerTrain
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context


class CandidateStateTest(unittest.TestCase):
    def test_candidate_is_scoped_and_retains_digest_validation(self):
        version='2.9.34-dev.20261108.1806'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'build-trains';target=state/'docker/completed'/f'{version}.json'
            target.parent.mkdir(parents=True)
            payload={'version':version,'images':[{'image':'cedar-test','reference':'registry/cedar-test:'+version,'digest':'sha256:'+'a'*64}]}
            target.write_text(json.dumps(payload))
            env={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','GITHUB_WORKSPACE':str(root)}
            with use_context(InvocationContext(environment=env)):
                with DockerTrain.verified_state(state):
                    self.assertEqual(DockerTrain.completion(version),payload)
                    self.assertEqual(DockerTrain.resolve(version),version)
                    payload['images'][0]['digest']='bad';target.write_text(json.dumps(payload))
                    with self.assertRaisesRegex(ValueError,'invalid image digest'):DockerTrain.completion(version)
                    with self.assertRaisesRegex(ValueError,'Docker verification'):DockerTrain._read('../outside.json')
                with patch.object(BuildTrain,'_read',return_value={'remote':True}) as read:
                    self.assertEqual(DockerTrain._read('docker/current.json'),{'remote':True})
                    read.assert_called_once()

    def test_no_candidate_override_on_workstation_or_outside_runner_state(self):
        with tempfile.TemporaryDirectory() as root:
            for env,expected in [({},'GitHub-hosted'),
                ({'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'self-hosted','GITHUB_WORKSPACE':root},'GitHub-hosted'),
                ({'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','GITHUB_WORKSPACE':root},'GITHUB_WORKSPACE')]:
                with use_context(InvocationContext(environment=env)), self.assertRaisesRegex(ValueError,expected):
                    with DockerTrain.verified_state(root):pass

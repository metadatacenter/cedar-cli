import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import build, publish
from org.metadatacenter.util.BuildTrain import BuildTrain, DockerTrain, NpmTrain
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.BuildTrainWorker import BuildTrainWorker


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class BuildTrainTest(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_allocate_uses_parent_snapshot_and_utc_minute(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Util, 'cedar_home', directory):
            parent = Path(directory) / 'cedar-parent'
            parent.mkdir()
            (parent / 'pom.xml').write_text(
                '<project><artifactId>cedar-parent</artifactId><version>2.9.3-SNAPSHOT</version></project>',
                encoding='utf-8',
            )
            self.assertEqual(
                '2.9.3-dev.20260824.1847',
                BuildTrain.allocate(dt.datetime(2026, 8, 24, 18, 47, tzinfo=dt.timezone.utc)),
            )

    def test_current_reads_completed_pointer(self):
        def opener(url, timeout):
            self.assertTrue(url.endswith('/current.json'))
            self.assertEqual(15, timeout)
            return Response(json.dumps({'version': '2.9.3-dev.20260824.1847'}).encode())

        self.assertEqual(
            '2.9.3-dev.20260824.1847',
            BuildTrain.current(environment={}, opener=opener),
        )

    def test_docker_current_reads_only_the_verified_image_pointer(self):
        def opener(url, timeout):
            self.assertTrue(url.endswith('/docker/current.json'))
            self.assertEqual(15, timeout)
            return Response(json.dumps({'version': '2.9.3-dev.20260824.1847'}).encode())

        self.assertEqual(
            '2.9.3-dev.20260824.1847',
            DockerTrain.current(environment={}, opener=opener),
        )

    def test_docker_completion_validates_immutable_image_inventory(self):
        version = '2.9.3-dev.20260824.1847'

        def opener(url, timeout):
            self.assertTrue(url.endswith(f'/docker/completed/{version}.json'))
            self.assertEqual(15, timeout)
            return Response(json.dumps({
                'version': version,
                'images': [{
                    'image': 'cedar-server-artifact',
                    'reference': f'registry.example/cedar-server-artifact:{version}',
                    'digest': 'sha256:' + 'a' * 64,
                }],
            }).encode())

        completion = DockerTrain.completion(version, opener=opener)
        self.assertEqual('sha256:' + 'a' * 64, completion['images'][0]['digest'])

    def test_docker_completion_rejects_an_invalid_digest(self):
        version = '2.9.3-dev.20260824.1847'

        def opener(_url, timeout):
            self.assertEqual(15, timeout)
            return Response(json.dumps({
                'version': version,
                'images': [{
                    'image': 'cedar-server-artifact',
                    'reference': f'registry.example/cedar-server-artifact:{version}',
                    'digest': 'latest',
                }],
            }).encode())

        with self.assertRaisesRegex(ValueError, 'invalid image digest'):
            DockerTrain.completion(version, opener=opener)

    def test_npm_current_reads_only_the_verified_package_pointer(self):
        def opener(url, timeout):
            self.assertTrue(url.endswith('/npm/current.json'))
            self.assertEqual(15, timeout)
            return Response(json.dumps({'version': '2.9.3-dev.20260824.1847'}).encode())

        self.assertEqual(
            '2.9.3-dev.20260824.1847',
            NpmTrain.current(environment={}, opener=opener),
        )

    @patch.object(BuildTrain, 'allocate', return_value='2.9.3-dev.20260824.1847')
    @patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run')
    def test_cli_allocates_and_dispatches_a_new_train(self, run, allocate):
        run.return_value.returncode = 0
        result = self.runner.invoke(publish.app, ['train'])
        self.assertEqual(0, result.exit_code, result.output)
        allocate.assert_called_once_with()
        self.assertIn('version=2.9.3-dev.20260824.1847', run.call_args.args[0])
        self.assertIn('resume=false', run.call_args.args[0])
        self.assertIn('develop', run.call_args.args[0])

    def test_cli_does_not_expose_a_version_option(self):
        result = self.runner.invoke(publish.app, [
            'train', '--version', '2.9.3-dev.20260824.1847',
        ])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn('No such option', result.output)

    @patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run')
    def test_cli_resume_uses_recorded_id(self, run):
        run.return_value.returncode = 0
        result = self.runner.invoke(publish.app, [
            'train', '--resume', '2.9.3-dev.20260824.1847',
        ])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('resume=true', run.call_args.args[0])

    def test_cli_dry_run_checks_but_never_dispatches(self):
        subprocess_result = type('Result', (), {
            'returncode': 0, 'stdout': '', 'stderr': '',
        })()
        with (
            patch.object(BuildTrain, 'allocate', return_value='2.9.3-dev.20260824.1847'),
            patch.object(
                BuildTrain, '_read',
                side_effect=ValueError('build-train state does not exist'),
            ),
            patch.object(
                BuildTrainWorker, '_configuration_summary',
                return_value=(44, 'cedar-model-typescript-library',
                              'cedar-embeddable-editor', 7, 3),
            ),
            patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run',
                  return_value=subprocess_result) as run,
        ):
            result = self.runner.invoke(publish.app, ['train', '--dry-run'])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('DRY RUN', result.output)
        self.assertIn('44 repositories', result.output)
        self.assertIn('7 frontends', result.output)
        self.assertIn('Would dispatch:', result.output)
        self.assertIn('No changes made.', result.output)
        self.assertEqual(2, run.call_count)
        self.assertFalse(any(call.args[0][1:3] == ['workflow', 'run'] for call in run.call_args_list))

    def test_cli_dry_run_refuses_a_train_id_collision(self):
        with (
            patch.object(BuildTrain, 'allocate', return_value='2.9.3-dev.20260824.1847'),
            patch.object(
                BuildTrain, '_read',
                return_value={'version': '2.9.3-dev.20260824.1847'},
            ),
            patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run') as run,
        ):
            result = self.runner.invoke(publish.app, ['train', '--dry-run'])
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn('already exists', result.output)
        run.assert_not_called()

    def test_cli_resume_dry_run_reports_the_next_incomplete_stage(self):
        version = '2.9.3-dev.20260824.1847'

        def state(path):
            if path in {f'trains/{version}.json', f'completed/{version}.json'}:
                return {'version': version}
            raise ValueError('build-train state does not exist')

        subprocess_result = type('Result', (), {
            'returncode': 0, 'stdout': '', 'stderr': '',
        })()
        with (
            patch.object(BuildTrain, '_read', side_effect=state),
            patch.object(
                BuildTrainWorker, '_configuration_summary',
                return_value=(44, 'cedar-model-typescript-library',
                              'cedar-embeddable-editor', 7, 3),
            ),
            patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run',
                  return_value=subprocess_result) as run,
        ):
            result = self.runner.invoke(publish.app, [
                'train', '--resume', version, '--dry-run',
            ])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('Mode: resume', result.output)
        self.assertIn('Next incomplete stage: npm plan', result.output)
        self.assertIn('resume=true', result.output)
        self.assertFalse(any(call.args[0][1:3] == ['workflow', 'run'] for call in run.call_args_list))

    def test_train_configuration_summary_validates_the_npm_graph(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Util, 'cedar_home', directory):
            ops = Path(directory) / 'cedar-development' / 'ops'
            ops.mkdir(parents=True)
            (ops / 'build-train.json').write_text(json.dumps({
                'organization': 'metadatacenter',
                'sourceBranch': 'develop',
                'repositories': ['model', 'cee', 'frontend', 'demo'],
            }), encoding='utf-8')
            (ops / 'frontend-train.json').write_text(json.dumps({
                'model': {'repository': 'model'},
                'cee': {'repository': 'cee'},
                'frontends': [{
                    'id': 'main', 'image': 'frontend-main',
                    'repository': 'frontend', 'npmVersionVariable': 'MAIN_VERSION',
                }],
                'additionalCeeConsumers': [{'repository': 'demo'}],
            }), encoding='utf-8')
            self.assertEqual(
                (4, 'model', 'cee', 1, 1),
                BuildTrainWorker._configuration_summary(),
            )

    def test_build_no_longer_exposes_train(self):
        result = self.runner.invoke(build.app, ['train'])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn('No such command', result.output)

    @patch.object(BuildTrain, '_read')
    def test_cli_reports_each_persisted_train_stage(self, read):
        read.side_effect = lambda path: (
            {'version': '2.9.3-dev.20260824.1847'}
            if path.startswith(('trains/', 'completed/', 'npm/'))
            else (_ for _ in ()).throw(ValueError('build-train state does not exist'))
        )
        result = self.runner.invoke(publish.app, [
            'train-status', '2.9.3-dev.20260824.1847',
        ])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('npm: recorded', result.output)
        self.assertIn('Docker: pending', result.output)
        self.assertIn(
            'https://github.com/metadatacenter/cedar-development/blob/'
            'build-trains/npm/completed/2.9.3-dev.20260824.1847.json',
            result.output,
        )
        self.assertIn(BuildTrain.STATE_BROWSE_URL, result.output)
        self.assertNotIn(f'Manifests: {BuildTrain.STATE_BASE_URL}/', result.output)


if __name__ == '__main__':
    unittest.main()

import datetime as dt
import io
import json
import os
import tempfile
from types import SimpleNamespace
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
    @patch.object(BuildTrainWorker, '_preflight', return_value=((44, 'model', 'cee', 7, 3), None))
    @patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run')
    def test_cli_allocates_and_dispatches_a_new_train(self, run, preflight, allocate):
        run.return_value.returncode = 0
        run.return_value.stdout = (
            'https://github.com/metadatacenter/cedar-development/actions/runs/33097135798\n'
        )
        run.return_value.stderr = ''
        result = self.runner.invoke(publish.app, ['train'])
        self.assertEqual(0, result.exit_code, result.output)
        allocate.assert_called_once_with()
        preflight.assert_called_once_with('2.9.3-dev.20260824.1847', None)
        self.assertIn('version=2.9.3-dev.20260824.1847', run.call_args.args[0])
        self.assertIn('resume=false', run.call_args.args[0])
        self.assertIn('develop', run.call_args.args[0])
        self.assertIn(
            'Compact live summary: cedarcli publish train-status '
            '2.9.3-dev.20260824.1847 --watch',
            result.output,
        )
        self.assertIn(
            'Detailed GitHub output: gh run watch 33097135798 '
            '--repo metadatacenter/cedar-development --compact --exit-status',
            result.output,
        )
        self.assertNotIn('Detailed live output: gh run list', result.output)

    @patch.object(BuildTrain, 'allocate', return_value='2.9.3-dev.20260824.1847')
    @patch.object(BuildTrainWorker, '_preflight', return_value=((44, 'model', 'cee', 7, 3), None))
    @patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run')
    def test_cli_does_not_call_a_run_listing_a_follow_command(self, run, _allocate, _preflight):
        run.return_value.returncode = 0
        run.return_value.stdout = ''
        run.return_value.stderr = ''
        result = self.runner.invoke(publish.app, ['train'])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('did not return the exact run ID', result.output)
        self.assertIn('Find it with: gh run list', result.output)
        self.assertNotIn('Detailed live output: gh run list', result.output)

    def test_cli_does_not_expose_a_version_option(self):
        result = self.runner.invoke(publish.app, [
            'train', '--version', '2.9.3-dev.20260824.1847',
        ])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn('No such option', result.output)

    @patch.object(BuildTrainWorker, '_preflight', return_value=((44, 'model', 'cee', 7, 3), None))
    @patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run')
    def test_cli_resume_uses_recorded_id(self, run, _preflight):
        run.return_value.returncode = 0
        result = self.runner.invoke(publish.app, [
            'train', '--resume', '2.9.3-dev.20260824.1847',
        ])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('resume=true', run.call_args.args[0])

    @patch.object(BuildTrainWorker, '_open_work', return_value=[])
    def test_cli_dry_run_checks_but_never_dispatches(self, _open_work):
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
            patch.object(BuildTrainWorker, '_local_configuration_preflight') as local_config,
            patch.object(BuildTrainWorker, '_source_alignment', return_value=[]),
            patch.object(BuildTrainWorker, '_source_ci_preflight') as source_ci,
            patch.object(BuildTrainWorker, '_npm_configuration_preflight') as npm_config,
            patch.object(BuildTrainWorker, '_publication_targets_preflight') as targets,
            patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run',
                  return_value=subprocess_result) as run,
        ):
            result = self.runner.invoke(publish.app, ['train', '--dry-run'])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('DRY RUN', result.output)
        self.assertIn('prospective ID; not reserved', result.output)
        self.assertIn('44 repositories', result.output)
        self.assertIn('7 frontends', result.output)
        self.assertIn('Would dispatch:', result.output)
        self.assertIn('No changes made.', result.output)
        self.assertEqual(3, run.call_count)
        local_config.assert_called_once_with()
        targets.assert_called_once_with()
        source_ci.assert_called_once_with(None)
        npm_config.assert_called_once_with()
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

    @patch.object(BuildTrainWorker, '_open_work', return_value=[])
    def test_cli_resume_dry_run_reports_the_next_incomplete_stage(self, _open_work):
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
            patch.object(BuildTrainWorker, '_source_alignment', return_value=[]),
            patch.object(BuildTrainWorker, '_source_ci_preflight'),
            patch.object(BuildTrainWorker, '_npm_configuration_preflight'),
            patch.object(BuildTrainWorker, '_publication_targets_preflight'),
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
                'mavenRepositories': ['model'],
                'phases': [{'name': 'model', 'repository': 'model'}],
                'requiredArtifacts': ['model'],
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
            (ops / 'docker-train.json').write_text(json.dumps({
                'groups': {
                    'javaBase': ['java'],
                    'microserviceBase': ['microservice'],
                    'infrastructure': [f'infra-{index}' for index in range(7)],
                    'microservices': [f'server-{index}' for index in range(21)],
                    'frontends': ['frontend-main'],
                },
            }), encoding='utf-8')
            self.assertEqual(
                (4, 'model', 'cee', 1, 1),
                BuildTrainWorker._configuration_summary(),
            )

    def test_build_no_longer_exposes_train(self):
        result = self.runner.invoke(build.app, ['train'])
        self.assertNotEqual(0, result.exit_code)
        self.assertIn('No such command', result.output)

    @patch.object(BuildTrainWorker, '_workflow_run', return_value=None)
    @patch.object(BuildTrain, '_read')
    def test_cli_reports_each_persisted_train_stage(self, read, _workflow):
        read.side_effect = lambda path: (
            {'version': '2.9.3-dev.20260824.1847'}
            if path.startswith(('trains/', 'completed/', 'npm/'))
            else (_ for _ in ()).throw(ValueError('build-train state does not exist'))
        )
        result = self.runner.invoke(publish.app, [
            'train-status', '2.9.3-dev.20260824.1847',
        ])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('npm model: recorded', result.output)
        self.assertIn('npm CEE: recorded', result.output)
        self.assertIn('npm frontends: recorded', result.output)
        self.assertIn('Docker: pending', result.output)
        self.assertIn(
            'https://github.com/metadatacenter/cedar-development/blob/'
            'build-trains/npm/completed/2.9.3-dev.20260824.1847.json',
            result.output,
        )
        self.assertIn(BuildTrain.STATE_BROWSE_URL, result.output)
        self.assertNotIn(f'Manifests: {BuildTrain.STATE_BASE_URL}/', result.output)
        self.assertIn('source state is recorded and publication is incomplete', result.output)
        self.assertIn('cedarcli publish train --resume', result.output)

    def test_local_publication_preflight_uses_maven_settings_without_exposing_password(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            settings = home / '.m2' / 'settings.xml'
            settings.parent.mkdir(parents=True)
            settings.write_text(
                '<settings><servers><server><id>bmir-nexus-releases</id>'
                '<username>settings-user</username><password>settings-secret</password>'
                '</server></servers></settings>',
                encoding='utf-8',
            )
            cedar_home = Path(directory) / 'CEDAR'
            controller = cedar_home / 'cedar-development' / 'ops' / 'build_train.py'
            controller.parent.mkdir(parents=True)
            controller.write_text('# controller\n', encoding='utf-8')
            completed = type('Completed', (), {
                'returncode': 0,
                'stdout': 'OK Nexus service status\nOK Docker registry authentication\n',
                'stderr': '',
            })()
            with patch.object(Util, 'cedar_home', str(cedar_home)), \
                    patch.dict(os.environ, {'HOME': str(home)}, clear=True), \
                    patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run',
                          return_value=completed) as run:
                BuildTrainWorker._publication_targets_preflight()

        environment = run.call_args.kwargs['env']
        self.assertEqual('settings-user', environment['BMIR_NEXUS_USERNAME'])
        self.assertEqual('settings-secret', environment['BMIR_NEXUS_PASSWORD'])
        self.assertNotIn('settings-secret', completed.stdout)
        self.assertEqual('probe-publication', run.call_args.args[0][-1])

    def test_failed_run_names_subcheck_and_selects_resume_for_recorded_source(self):
        version = '2.9.3-dev.20260824.1847'

        def state(path):
            if path == f'trains/{version}.json':
                return {'version': version}
            raise ValueError('build-train state does not exist')

        workflow = {'databaseId': 12, 'status': 'completed', 'conclusion': 'failure'}
        progress = {
            'status': 'completed', 'conclusion': 'failure', 'url': 'https://example/run/12',
            'jobs': [{
                'name': 'npm 2/3 · wire model and publish CEE',
                'status': 'completed', 'conclusion': 'failure',
                'steps': [{'name': 'Gate CEE', 'conclusion': 'failure'}],
            }],
        }
        with patch.object(BuildTrain, '_read', side_effect=state), \
                patch.object(BuildTrainWorker, '_workflow_run', return_value=workflow), \
                patch.object(BuildTrainWorker, '_workflow_progress', return_value=progress):
            result = self.runner.invoke(publish.app, ['train-status', version])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn('Failed subcheck: npm 2/3', result.output)
        self.assertIn('Gate CEE', result.output)
        self.assertIn(f'--resume {version}', result.output)
        self.assertIn('--dry-run', result.output)
        self.assertIn('Publication may be partial', result.output)

    def test_failure_before_source_state_requires_a_new_train(self):
        version = '2.9.3-dev.20260824.1847'
        with patch.object(
                BuildTrain, '_read', side_effect=ValueError('build-train state does not exist')), \
                patch.object(BuildTrainWorker, '_workflow_run', return_value=None):
            result = self.runner.invoke(publish.app, ['train-status', version])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('no source state was recorded; use a new train ID', result.output)
        self.assertIn('Publication: none can have started', result.output)
        self.assertIn('Recommended command: cedarcli publish train', result.output)

    def test_watch_reports_compact_matrix_counts_then_completion(self):
        version = '2.9.3-dev.20260824.1847'
        workflow = {'databaseId': 13, 'status': 'in_progress', 'conclusion': None}
        running = {
            'status': 'in_progress', 'conclusion': None, 'url': 'https://example/run/13',
            'jobs': [
                {'name': 'publish-maven', 'status': 'completed', 'conclusion': 'success'},
                {'name': 'npm 1/3 · model', 'status': 'completed', 'conclusion': 'success'},
                {'name': 'java-base', 'status': 'completed', 'conclusion': 'success'},
            ],
        }
        complete = {
            'status': 'completed', 'conclusion': 'success', 'url': 'https://example/run/13',
            'jobs': running['jobs'],
        }
        with patch.object(BuildTrain, '_read', return_value={'version': version}), \
                patch.object(BuildTrainWorker, '_workflow_run', return_value=workflow), \
                patch.object(
                    BuildTrainWorker, '_workflow_progress', side_effect=[running, complete]), \
                patch('org.metadatacenter.worker.BuildTrainWorker.time.sleep') as sleep:
            result = self.runner.invoke(publish.app, ['train-status', version, '--watch'])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('npm 1/3 done', result.output)
        self.assertIn('images 1/31 done', result.output)
        self.assertIn('Decision: complete; do not resume or abandon', result.output)
        sleep.assert_called_once_with(10)

    def test_watch_heartbeats_with_active_step_when_stage_counts_do_not_change(self):
        version = '2.9.3-dev.20260824.1847'
        workflow = {'databaseId': 13, 'status': 'in_progress', 'conclusion': None}
        running = {
            'status': 'in_progress', 'conclusion': None, 'url': 'https://example/run/13',
            'jobs': [{
                'name': 'publish-maven', 'status': 'in_progress', 'conclusion': None,
                'steps': [{
                    'name': 'Build and publish the Maven train',
                    'status': 'in_progress', 'conclusion': None,
                }],
            }],
        }
        complete = {
            'status': 'completed', 'conclusion': 'success', 'url': 'https://example/run/13',
            'jobs': [{
                'name': 'publish-maven', 'status': 'completed', 'conclusion': 'success',
                'steps': [],
            }],
        }
        with patch.object(BuildTrain, '_read', return_value={'version': version}), \
                patch.object(BuildTrainWorker, '_workflow_run', return_value=workflow), \
                patch.object(
                    BuildTrainWorker, '_workflow_progress',
                    side_effect=[running, running, complete]), \
                patch('org.metadatacenter.worker.BuildTrainWorker.time.monotonic',
                      side_effect=[0, 0, 61]), \
                patch('org.metadatacenter.worker.BuildTrainWorker.time.sleep'):
            result = self.runner.invoke(publish.app, ['train-status', version, '--watch'])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('active publish-maven —', result.output)
        self.assertIn('Build and publish the Maven train', result.output)
        self.assertIn('elapsed 0:01:01', result.output)


if __name__ == '__main__':
    unittest.main()


class OpenWorkRefusalTest(unittest.TestCase):
    """A train is built from GitHub, so local work it cannot see must stop the dispatch.

    The failure this prevents is silent: the train reports success, its images are built and
    verified, and the change someone believed they were shipping is in none of them.
    """

    def _home(self, directory, repositories):
        ops = Path(directory) / 'cedar-development' / 'ops'
        ops.mkdir(parents=True)
        (ops / 'build-train.json').write_text(json.dumps({
            'organization': 'metadatacenter',
            'sourceBranch': 'develop',
            'repositories': repositories,
        }), encoding='utf-8')
        for repository in repositories:
            (Path(directory) / repository / '.git').mkdir(parents=True)
        return directory

    def test_a_clean_estate_reports_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            self._home(directory, ['cedar-a', 'cedar-b'])
            with patch.object(Util, 'cedar_home', directory), \
                    patch.object(BuildTrainWorker, '_git', return_value=(0, '', '')):
                self.assertEqual([], BuildTrainWorker._open_work())

    def test_an_uncommitted_change_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            self._home(directory, ['cedar-a'])

            def git(_root, *arguments):
                if arguments[0] == 'status':
                    return 0, ' M pom.xml\n M src/Main.java', ''
                return 0, '0', ''

            with patch.object(Util, 'cedar_home', directory), \
                    patch.object(BuildTrainWorker, '_git', side_effect=git):
                findings = BuildTrainWorker._open_work()
            self.assertEqual(1, len(findings))
            self.assertIn('cedar-a has 2 uncommitted change(s)', findings[0])

    def test_an_unpushed_commit_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            self._home(directory, ['cedar-a'])

            def git(_root, *arguments):
                if arguments[0] == 'status':
                    return 0, '', ''
                return 0, '2', ''

            with patch.object(Util, 'cedar_home', directory), \
                    patch.object(BuildTrainWorker, '_git', side_effect=git):
                findings = BuildTrainWorker._open_work()
            self.assertEqual(1, len(findings))
            self.assertIn('cedar-a has 2 unpushed commit(s) on develop', findings[0])

    def test_a_repository_absent_from_this_machine_is_not_a_finding(self):
        """The train reads GitHub; a repository not checked out here holds no local work."""
        with tempfile.TemporaryDirectory() as directory:
            ops = Path(directory) / 'cedar-development' / 'ops'
            ops.mkdir(parents=True)
            (ops / 'build-train.json').write_text(json.dumps({
                'organization': 'metadatacenter',
                'sourceBranch': 'develop',
                'repositories': ['cedar-not-cloned'],
            }), encoding='utf-8')
            with patch.object(Util, 'cedar_home', directory):
                self.assertEqual([], BuildTrainWorker._open_work())

    def test_source_alignment_requires_develop_and_the_live_remote_head(self):
        with tempfile.TemporaryDirectory() as directory:
            self._home(directory, ['cedar-a'])

            def git(_root, *arguments):
                if arguments[:2] == ('rev-parse', '--abbrev-ref'):
                    return 0, 'feature', ''
                if arguments[:2] == ('rev-parse', 'refs/heads/develop'):
                    return 0, 'a' * 40, ''
                if arguments[0] == 'ls-remote':
                    return 0, f"{'b' * 40}\trefs/heads/develop", ''
                return 0, '', ''

            with patch.object(Util, 'cedar_home', directory), \
                    patch.object(BuildTrainWorker, '_git', side_effect=git):
                findings = BuildTrainWorker._source_alignment()

        self.assertEqual(2, len(findings))
        self.assertIn('not develop', findings[0])
        self.assertIn('local develop is', findings[1])

    def test_an_active_train_blocks_a_second_dispatch(self):
        result = type('Result', (), {
            'returncode': 0,
            'stdout': '123\tin_progress\tBuild train 2.9.5-dev.20260901.1200\n',
            'stderr': '',
        })()
        with patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run',
                   return_value=result):
            active = BuildTrainWorker._active_workflow_runs()

        self.assertEqual(1, len(active))
        self.assertIn('in_progress', active[0])

    def test_local_train_preflight_checks_the_exact_develop_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            self._home(directory, ['cedar-a'])
            workflows = Path(directory) / 'cedar-a' / '.github' / 'workflows'
            workflows.mkdir(parents=True)
            (workflows / 'ci.yml').write_text('name: CI\n', encoding='utf-8')
            run = {
                'name': 'CI', 'status': 'completed', 'conclusion': 'success',
                'id': 7,
            }
            with (
                    patch.object(Util, 'cedar_home', directory),
                    patch.object(BuildTrainWorker, '_git', return_value=(
                        0, f"{'a' * 40}\trefs/heads/develop", '')),
                    patch(
                        'org.metadatacenter.worker.BuildTrainWorker.probe_exact_commit',
                        return_value=SimpleNamespace(runs=(run,)),
                    ) as probe,
            ):
                BuildTrainWorker._source_ci_preflight()

        probe.assert_called_once()
        self.assertEqual('cedar-a', probe.call_args.args[0])
        self.assertEqual('a' * 40, probe.call_args.args[1])

    def test_local_train_preflight_refuses_pending_ci_with_run_url(self):
        with tempfile.TemporaryDirectory() as directory:
            self._home(directory, ['cedar-a'])
            workflows = Path(directory) / 'cedar-a' / '.github' / 'workflows'
            workflows.mkdir(parents=True)
            (workflows / 'ci.yml').write_text('name: CI\n', encoding='utf-8')
            run = {
                'name': 'CI', 'status': 'in_progress', 'conclusion': None,
                'id': 7, 'html_url': 'https://github.example/runs/7',
            }
            with (
                    patch.object(Util, 'cedar_home', directory),
                    patch.object(BuildTrainWorker, '_git', return_value=(
                        0, f"{'a' * 40}\trefs/heads/develop", '')),
                    patch(
                        'org.metadatacenter.worker.BuildTrainWorker.probe_exact_commit',
                        return_value=SimpleNamespace(runs=(run,)),
                    ),
            ):
                with self.assertRaisesRegex(ValueError, 'in_progress.*runs/7'):
                    BuildTrainWorker._source_ci_preflight()

    def test_local_train_requires_cedar_development_source_validation_ci(self):
        with tempfile.TemporaryDirectory() as directory:
            self._home(directory, ['cedar-development'])
            workflows = Path(directory) / 'cedar-development' / '.github' / 'workflows'
            workflows.mkdir(parents=True)
            (workflows / 'build-train.yml').write_text('name: train\n', encoding='utf-8')
            (workflows / 'release-tooling-ci.yml').write_text('name: CI\n', encoding='utf-8')
            only_train = {
                'name': 'Immutable development build train', 'status': 'in_progress',
                'conclusion': None, 'path': '.github/workflows/build-train.yml',
            }
            with (
                    patch.object(Util, 'cedar_home', directory),
                    patch.object(BuildTrainWorker, '_git', return_value=(
                        0, f"{'a' * 40}\trefs/heads/develop", '')),
                    patch(
                        'org.metadatacenter.worker.BuildTrainWorker.probe_exact_commit',
                        return_value=SimpleNamespace(runs=(only_train,)),
                    ),
            ):
                with self.assertRaisesRegex(ValueError, 'cedar-development: no CI run'):
                    BuildTrainWorker._source_ci_preflight()

    def test_train_preflight_names_unsafe_npmrc_keys_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            npmrc = Path(directory) / '.npmrc'
            npmrc.write_text(
                'always-auth=true\n//registry.example/:_authToken=secret\n',
                encoding='utf-8',
            )
            with patch.dict(os.environ, {'NPM_CONFIG_USERCONFIG': str(npmrc)}, clear=False):
                with self.assertRaisesRegex(ValueError, 'publication authentication'):
                    BuildTrainWorker._npm_configuration_preflight()

    def test_dispatch_refuses_and_runs_no_workflow(self):
        with patch.object(BuildTrainWorker, '_preflight', side_effect=ValueError(
                'source repositories hold work the train cannot see')), \
                patch.object(BuildTrain, 'allocate', return_value='2.9.4-dev.20260901.0400'), \
                patch('org.metadatacenter.worker.BuildTrainWorker.subprocess.run') as run:
            self.assertEqual(1, BuildTrainWorker.dispatch())
        run.assert_not_called()

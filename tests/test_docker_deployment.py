import json
import os
import tempfile
import unittest
from unittest.mock import call, patch

from typer.testing import CliRunner

from org.metadatacenter import docker, docker_start, docker_stop
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.util.BuildTrain import BuildTrain, DockerTrain
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.DockerWorker import DockerWorker


def deployment_environment():
    environment = {
        'CEDAR_HOST': 'metadatacenter.orgx',
        'CEDAR_NGINX_HOST': '192.168.17.207',
    }
    for index, frontend in enumerate((
            'EDITOR', 'CONTENT', 'OPENVIEW', 'MONITORING', 'BRIDGING', 'WORKSPACE', 'DESIGNER'), 151):
        environment[f'CEDAR_FRONTEND_{frontend}_CONTAINER_HOST'] = f'192.168.17.{index}'
        environment[f'CEDAR_FRONTEND_{frontend}_HOST'] = 'stale-value'
    return environment


class DockerDeploymentTest(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    @patch.object(DockerTrain, 'resolve', return_value='2.9.3-dev.20260824.1847')
    @patch.object(DockerWorker, 'start_all', return_value=0)
    def test_start_all_cli_passes_mode_pull_timeout_and_admin(self, start_all, resolve):
        result = self.runner.invoke(docker_start.app, [
            'all', '--mode', 'hybrid', '--pull', 'missing', '--timeout', '42', '--include-admin',
        ])

        self.assertEqual(0, result.exit_code, result.output)
        start_all.assert_called_once_with(
            mode=DockerDeploymentMode.HYBRID,
            pull='missing',
            timeout=42,
            include_admin=True,
            train='2.9.3-dev.20260824.1847',
        )
        resolve.assert_called_once_with(None)

    @patch.object(DockerTrain, 'resolve')
    @patch.object(DockerWorker, 'start_all', return_value=0)
    def test_start_all_local_does_not_resolve_a_published_train(self, start_all, resolve):
        result = self.runner.invoke(docker_start.app, [
            'all', '--mode', 'backend', '--local',
        ])

        self.assertEqual(0, result.exit_code, result.output)
        resolve.assert_not_called()
        self.assertIsNone(start_all.call_args.kwargs['train'])

    @patch.object(DockerTrain, 'resolve')
    @patch.object(DockerWorker, 'start_infrastructure', return_value=0)
    @patch.object(DockerWorker, 'active_train', return_value='2.9.3-dev.20260824.1847')
    @patch.object(DockerWorker, 'active_deployment', return_value=(DockerDeploymentMode.FULL, False))
    def test_individual_start_preserves_the_active_train(
            self, _active, _active_train, start, resolve):
        result = self.runner.invoke(docker_start.app, ['infrastructure'])

        self.assertEqual(0, result.exit_code, result.output)
        resolve.assert_not_called()
        start.assert_called_once_with(False, 'never', '2.9.3-dev.20260824.1847')

    @patch.object(DockerWorker, 'stop_all', return_value=0)
    def test_stop_all_cli_passes_admin_selection(self, stop_all):
        result = self.runner.invoke(docker_stop.app, ['all', '--include-admin'])

        self.assertEqual(0, result.exit_code, result.output)
        stop_all.assert_called_once_with(include_admin=True)

    @patch.object(DockerWorker, 'status', return_value=True)
    def test_status_cli_accepts_an_explicit_mode(self, status):
        result = self.runner.invoke(docker.app, ['status', '--mode', 'backend'])

        self.assertEqual(0, result.exit_code, result.output)
        status.assert_called_once_with(mode=DockerDeploymentMode.BACKEND, include_admin=False)

    def test_mode_environment_keeps_container_addresses_separate_from_hybrid_upstreams(self):
        with patch.dict(os.environ, deployment_environment(), clear=True):
            full, full_errors = DockerWorker.mode_environment(DockerDeploymentMode.FULL)
            hybrid, hybrid_errors = DockerWorker.mode_environment(DockerDeploymentMode.HYBRID)

        self.assertEqual([], full_errors)
        self.assertEqual([], hybrid_errors)
        self.assertEqual('192.168.17.151', full['CEDAR_FRONTEND_EDITOR_HOST'])
        self.assertEqual('host.docker.internal', hybrid['CEDAR_FRONTEND_EDITOR_HOST'])
        self.assertEqual('192.168.17.151', hybrid['CEDAR_FRONTEND_EDITOR_CONTAINER_HOST'])
        self.assertEqual('192.168.17.207', hybrid['CEDAR_AUTH_HOST_TARGET'])

    def test_mode_environment_reports_every_missing_mode_input(self):
        with patch.dict(os.environ, {}, clear=True):
            _environment, errors = DockerWorker.mode_environment(DockerDeploymentMode.FULL)

        self.assertIn('CEDAR_HOST is not defined', errors)
        self.assertIn('CEDAR_NGINX_HOST is not defined', errors)
        self.assertIn(
            'CEDAR Docker profile is not loaded; source '
            '$CEDAR_HOME/cedar-development/bin/templates/cedar-profile-docker.sh',
            errors,
        )

    def test_mode_environment_reports_a_partial_profile_exactly(self):
        environment = deployment_environment()
        del environment['CEDAR_FRONTEND_DESIGNER_CONTAINER_HOST']
        with patch.dict(os.environ, environment, clear=True):
            _environment, errors = DockerWorker.mode_environment(DockerDeploymentMode.FULL)

        self.assertEqual(
            ['CEDAR_FRONTEND_DESIGNER_CONTAINER_HOST is not defined'],
            errors,
        )

    @patch.object(DockerWorker, '_docker_command')
    def test_backend_authentication_probe_accepts_the_local_certificate(self, command):
        command.return_value = type(
            'Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''},
        )()
        with patch.dict(os.environ, {'CEDAR_HOST': 'metadatacenter.orgx'}, clear=True):
            self.assertIsNone(DockerWorker._backend_auth_error(timeout=7))

        command.assert_called_once_with([
            'exec', 'server-resource', 'curl', '-kfsS', '--max-time', '7',
            'https://auth.metadatacenter.orgx/realms/CEDAR/.well-known/openid-configuration',
        ])

    @patch.object(DockerWorker, '_record_active_deployment')
    @patch.object(DockerWorker, '_wait_for_acceptance', return_value=True)
    @patch.object(DockerWorker, '_wait_for_stacks', return_value=True)
    @patch.object(DockerWorker, 'compose', return_value=0)
    @patch.object(DockerWorker, 'preflight', return_value=True)
    @patch.object(DockerWorker, 'mode_environment', return_value=({'MODE': 'full'}, []))
    def test_full_start_orders_all_selected_stacks(
            self, _environment, _preflight, compose, wait, _acceptance, record):
        self.assertEqual(0, DockerWorker.start_all(
            DockerDeploymentMode.FULL,
            pull='always',
            timeout=90,
            include_admin=True,
        ))

        self.assertEqual([
            call('infrastructure', 'up', detach=True, pull='always', environment={'MODE': 'full'}),
            call('microservices', 'up', detach=True, pull='always', environment={'MODE': 'full'}),
            call('frontends', 'up', detach=True, pull='always', environment={'MODE': 'full'}),
            call('admin', 'up', detach=True, pull='always', environment={'MODE': 'full'}),
        ], compose.call_args_list)
        self.assertEqual([
            ['infrastructure'],
            ['infrastructure', 'microservices'],
            ['infrastructure', 'microservices', 'frontends'],
            ['infrastructure', 'microservices', 'frontends', 'admin'],
        ], [entry.args[0] for entry in wait.call_args_list])
        record.assert_called_once_with(DockerDeploymentMode.FULL, True)

    @patch.object(DockerWorker, '_record_active_deployment')
    @patch.object(DockerWorker, '_wait_for_acceptance', return_value=True)
    @patch.object(DockerWorker, '_wait_for_stacks', return_value=True)
    @patch.object(DockerWorker, 'compose', return_value=0)
    @patch.object(DockerWorker, 'preflight', return_value=True)
    @patch.object(DockerWorker, 'mode_environment', return_value=({'MODE': 'hybrid'}, []))
    def test_hybrid_stops_docker_frontends_then_starts_only_the_backend(
            self, _environment, _preflight, compose, _wait, _acceptance, record):
        self.assertEqual(0, DockerWorker.start_all(DockerDeploymentMode.HYBRID))

        self.assertEqual([
            call('frontends', 'down', environment={'MODE': 'hybrid'}),
            call('infrastructure', 'up', detach=True, pull='never', environment={'MODE': 'hybrid'}),
            call('microservices', 'up', detach=True, pull='never', environment={'MODE': 'hybrid'}),
        ], compose.call_args_list)
        record.assert_called_once_with(DockerDeploymentMode.HYBRID, False)

    @patch.object(DockerWorker, 'compose')
    @patch.object(DockerWorker, 'preflight', return_value=False)
    @patch.object(DockerWorker, 'mode_environment', return_value=({}, []))
    def test_preflight_failure_does_not_change_containers(self, _environment, _preflight, compose):
        self.assertEqual(1, DockerWorker.start_all(DockerDeploymentMode.BACKEND))
        compose.assert_not_called()

    @patch.object(DockerWorker, '_port_owned_by_selected_compose_project', return_value=False)
    @patch.object(DockerWorker, '_port_has_listener', return_value=True)
    @patch.object(DockerWorker, '_published_ports', return_value=([443], []))
    @patch.object(DockerWorker, '_docker_command')
    @patch.object(DockerWorker, '_docker_server_version', return_value=('29.6.2', None))
    @patch.object(DockerWorker, 'validate', return_value=0)
    def test_preflight_rejects_a_port_owned_outside_the_selected_projects(
            self, _validate, _version, command, _ports, _listener, _owner):
        command.return_value = type('Result', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        self.assertFalse(DockerWorker.preflight(
            DockerDeploymentMode.FULL,
            include_admin=False,
            environment={},
        ))

    @patch.object(DockerWorker, '_clear_active_deployment')
    @patch.object(DockerWorker, 'compose', return_value=0)
    @patch.object(DockerWorker, 'mode_environment', return_value=({}, []))
    @patch.object(DockerWorker, 'active_deployment', return_value=(DockerDeploymentMode.HYBRID, False))
    def test_stop_all_uses_reverse_dependency_order_and_preserves_admin_by_default(
            self, _active, _environment, compose, clear):
        self.assertEqual(0, DockerWorker.stop_all())
        self.assertEqual([
            call('frontends', 'down', environment={}),
            call('microservices', 'down', environment={}),
            call('infrastructure', 'down', environment={}),
        ], compose.call_args_list)
        clear.assert_called_once()

    @patch.object(DockerWorker, '_clear_active_deployment')
    @patch.object(DockerWorker, 'compose', return_value=0)
    @patch.object(DockerWorker, 'mode_environment', return_value=({}, []))
    @patch.object(DockerWorker, 'active_deployment', return_value=(DockerDeploymentMode.FULL, True))
    def test_stop_all_remembers_admin_selected_by_aggregate_start(
            self, _active, _environment, compose, _clear):
        self.assertEqual(0, DockerWorker.stop_all())
        self.assertEqual('admin', compose.call_args_list[0].args[0])

    @patch.object(DockerWorker, 'compose')
    @patch.object(DockerWorker, 'active_deployment', return_value=(DockerDeploymentMode.HYBRID, False))
    def test_individual_frontend_start_cannot_contradict_active_mode(self, _active, compose):
        self.assertEqual(1, DockerWorker.start_frontends(detach=True))
        compose.assert_not_called()

    def test_active_deployment_state_round_trips(self):
        with tempfile.TemporaryDirectory() as cedar_home, patch.object(Util, 'cedar_home', cedar_home):
            DockerWorker._record_active_deployment(DockerDeploymentMode.BACKEND, True)
            state_path = DockerWorker._deployment_state_path()
            with open(state_path, 'r', encoding='utf-8') as state_file:
                self.assertEqual(
                    {'mode': 'backend', 'include_admin': True},
                    json.load(state_file),
                )
            self.assertEqual(
                (DockerDeploymentMode.BACKEND, True),
                DockerWorker.active_deployment(),
            )
            self.assertIsNone(DockerWorker.active_train())
            DockerWorker._clear_active_deployment()
            self.assertEqual((None, False), DockerWorker.active_deployment())


if __name__ == '__main__':
    unittest.main()

import json
import os
import re
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from typer.testing import CliRunner

from org.metadatacenter import docker, docker_start, docker_stop
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.util.BuildTrain import BuildTrain, DockerTrain
from org.metadatacenter.util.ModeManager import ModeManager
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.DockerWorker import DockerWorker

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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
        self.surface_patch = patch.object(
            ModeManager, 'require_surface', return_value=CedarMode.DOCKER)
        self.surface_patch.start()
        self.start_safety_patch = patch.object(
            ModeManager, 'require_docker_start_compatible',
            side_effect=lambda mode: mode,
        )
        self.start_safety_patch.start()
        self.topology_patch = patch.object(
            ModeManager, 'docker_topology', return_value=DockerDeploymentMode.FULL)
        self.topology = self.topology_patch.start()

    def tearDown(self):
        self.topology_patch.stop()
        self.start_safety_patch.stop()
        self.surface_patch.stop()

    @patch.object(DockerTrain, 'resolve', return_value='2.9.3-dev.20260824.1847')
    @patch.object(DockerWorker, 'start_all', return_value=0)
    def test_start_all_cli_uses_configured_mode_pull_and_timeout(self, start_all, resolve):
        result = self.runner.invoke(docker_start.app, [
            'all', '--pull', 'missing', '--timeout', '42',
        ])

        self.assertEqual(0, result.exit_code, result.output)
        start_all.assert_called_once_with(
            mode=DockerDeploymentMode.FULL,
            pull='missing',
            timeout=42,
            train='2.9.3-dev.20260824.1847',
        )
        resolve.assert_called_once_with(None)

    @patch.object(DockerTrain, 'resolve')
    @patch.object(DockerWorker, 'start_all', return_value=0)
    def test_start_all_local_does_not_resolve_a_published_train(self, start_all, resolve):
        result = self.runner.invoke(docker_start.app, [
            'all', '--local',
        ])

        self.assertEqual(0, result.exit_code, result.output)
        resolve.assert_not_called()
        self.assertIsNone(start_all.call_args.kwargs['train'])

    @patch.object(DockerTrain, 'resolve')
    @patch.object(DockerWorker, 'start_infrastructure', return_value=0)
    @patch.object(DockerWorker, 'active_train', return_value='2.9.3-dev.20260824.1847')
    @patch.object(DockerWorker, 'active_deployment', return_value=DockerDeploymentMode.FULL)
    def test_individual_start_preserves_the_active_train(
            self, _active, _active_train, start, resolve):
        result = self.runner.invoke(docker_start.app, ['infra'])

        self.assertEqual(0, result.exit_code, result.output)
        resolve.assert_not_called()
        start.assert_called_once_with(False, 'never', '2.9.3-dev.20260824.1847')

    def test_individual_infrastructure_stack_is_named_infra(self):
        for command_group in (docker_start.app, docker_stop.app):
            self.assertEqual(0, self.runner.invoke(command_group, ['infra', '--help']).exit_code)
            self.assertEqual(2, self.runner.invoke(
                command_group, ['infrastructure', '--help']).exit_code)

    @patch.object(DockerWorker, 'start_frontend', return_value=0)
    @patch.object(DockerTrain, 'resolve')
    def test_individual_frontend_cli_uses_validated_target(self, resolve, start):
        result = self.runner.invoke(docker_start.app, [
            'frontend', 'designer', '--local', '--detach',
        ])

        self.assertEqual(0, result.exit_code, result.output)
        resolve.assert_not_called()
        start.assert_called_once_with('designer', True, 'never', None)

    def test_detach_only_accepts_the_long_option(self):
        help_result = self.runner.invoke(docker_start.app, ['infra', '--help'])
        short_result = self.runner.invoke(docker_start.app, ['infra', '-d'])

        self.assertEqual(0, help_result.exit_code, help_result.output)
        self.assertIn('--detach', ANSI_ESCAPE.sub('', help_result.output))
        self.assertEqual(2, short_result.exit_code, short_result.output)

    @patch.object(DockerWorker, 'stop_microservice', return_value=0)
    def test_individual_microservice_cli_uses_validated_target(self, stop):
        result = self.runner.invoke(docker_stop.app, ['microservice', 'open'])

        self.assertEqual(0, result.exit_code, result.output)
        stop.assert_called_once_with('open')

    @patch.object(DockerWorker, 'start_keycloak', return_value=0)
    @patch.object(DockerTrain, 'resolve')
    def test_keycloak_start_accepts_long_and_short_names(self, resolve, start):
        for target in ('keycloak', 'kk'):
            result = self.runner.invoke(docker_start.app, [target, '--local'])
            self.assertEqual(0, result.exit_code, result.output)

        resolve.assert_not_called()
        self.assertEqual(2, start.call_count)

    @patch.object(DockerWorker, 'stop_keycloak', return_value=0)
    def test_keycloak_stop_accepts_long_and_short_names(self, stop):
        for target in ('keycloak', 'kk'):
            result = self.runner.invoke(docker_stop.app, [target])
            self.assertEqual(0, result.exit_code, result.output)

        self.assertEqual(2, stop.call_count)

    @patch.object(DockerWorker, 'status', return_value=True)
    def test_status_cli_uses_the_configured_mode(self, status):
        self.topology.return_value = DockerDeploymentMode.HYBRID
        result = self.runner.invoke(docker.app, ['status'])

        self.assertEqual(0, result.exit_code, result.output)
        status.assert_called_once_with(mode=DockerDeploymentMode.HYBRID)

    def test_include_admin_option_is_not_exposed(self):
        for command_group, arguments in (
                (docker_start.app, ['all', '--include-admin']),
                (docker_stop.app, ['all', '--include-admin']),
                (docker.app, ['status', '--include-admin'])):
            result = self.runner.invoke(command_group, arguments)
            self.assertEqual(2, result.exit_code, result.output)

    def test_mode_option_is_not_exposed_on_docker_commands(self):
        start_result = self.runner.invoke(docker_start.app, [
            'all', '--mode', 'hybrid', '--local',
        ])
        status_result = self.runner.invoke(docker.app, ['status', '--mode', 'hybrid'])

        self.assertEqual(2, start_result.exit_code, start_result.output)
        self.assertEqual(2, status_result.exit_code, status_result.output)

    def test_docker_setup_exposes_only_bootstrap_operations(self):
        docker_help = self.runner.invoke(docker.app, ['--help'])
        result = self.runner.invoke(docker.app, ['setup', '--help'])
        output = ANSI_ESCAPE.sub('', result.output)

        self.assertEqual(0, docker_help.exit_code, docker_help.output)
        self.assertIn('setup', ANSI_ESCAPE.sub('', docker_help.output))
        self.assertEqual(0, result.exit_code, result.output)
        for command in (
                'one-time-setup', 'create-network',
                'create-certificates-volume', 'copy-certificates'):
            self.assertIn(command, output)

        old_path = self.runner.invoke(docker.app, ['one-time-setup'])
        self.assertEqual(2, old_path.exit_code, old_path.output)

    @patch.object(DockerWorker, 'copy_certificates', return_value=0)
    @patch.object(DockerWorker, 'create_certificates_volume', return_value=0)
    @patch.object(DockerWorker, 'create_network', return_value=0)
    def test_one_time_setup_runs_bootstrap_operations_in_order(
            self, create_network, create_volumes, copy_certificates):
        manager = Mock()
        manager.attach_mock(create_network, 'create_network')
        manager.attach_mock(create_volumes, 'create_volumes')
        manager.attach_mock(copy_certificates, 'copy_certificates')

        result = self.runner.invoke(docker.app, ['setup', 'one-time-setup'])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual([
            call.create_network(),
            call.create_volumes(),
            call.copy_certificates(),
        ], manager.mock_calls)

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
            'CEDAR Docker environment is incomplete; run '
            'cedarcli mode docker or cedarcli mode hybrid',
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
        ))

        self.assertEqual([
            call('infrastructure', 'up', detach=True, pull='always', environment={'MODE': 'full'}),
            call('microservices', 'up', detach=True, pull='always', environment={'MODE': 'full'}),
            call('frontends', 'up', detach=True, pull='always', environment={'MODE': 'full'}),
        ], compose.call_args_list)
        self.assertEqual([
            ['infrastructure'],
            ['infrastructure', 'microservices'],
            ['infrastructure', 'microservices', 'frontends'],
        ], [entry.args[0] for entry in wait.call_args_list])
        record.assert_called_once_with(DockerDeploymentMode.FULL)

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
        record.assert_called_once_with(DockerDeploymentMode.HYBRID)

    @patch.object(DockerWorker, 'compose')
    @patch.object(DockerWorker, 'preflight', return_value=False)
    @patch.object(DockerWorker, 'mode_environment', return_value=({}, []))
    def test_preflight_failure_does_not_change_containers(self, _environment, _preflight, compose):
        self.assertEqual(1, DockerWorker.start_all(DockerDeploymentMode.HYBRID))
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
            environment={},
        ))

    @patch.object(DockerWorker, '_clear_active_deployment')
    @patch.object(DockerWorker, 'compose', return_value=0)
    @patch.object(DockerWorker, 'mode_environment', return_value=({}, []))
    @patch.object(DockerWorker, 'active_deployment', return_value=DockerDeploymentMode.HYBRID)
    @patch.object(DockerWorker, '_docker_server_version', return_value=('29.6.2', None))
    def test_stop_all_uses_reverse_dependency_order(
            self, _daemon, _active, _environment, compose, clear):
        self.assertEqual(0, DockerWorker.stop_all(DockerDeploymentMode.HYBRID))
        self.assertEqual([
            call('frontends', 'down', environment={}),
            call('microservices', 'down', environment={}),
            call('infrastructure', 'down', environment={}),
        ], compose.call_args_list)
        clear.assert_called_once()

    @patch.object(DockerWorker, 'compose')
    @patch.object(
        DockerWorker, '_docker_server_version', return_value=(None, 'daemon unavailable'))
    def test_stop_all_reports_an_unavailable_daemon_once(self, _daemon, compose):
        self.assertEqual(1, DockerWorker.stop_all(DockerDeploymentMode.FULL))
        compose.assert_not_called()

    @patch.object(DockerWorker, 'compose')
    @patch.object(DockerWorker, 'active_deployment', return_value=DockerDeploymentMode.HYBRID)
    def test_individual_frontend_start_cannot_contradict_active_mode(self, _active, compose):
        self.assertEqual(1, DockerWorker.start_frontends(detach=True))
        compose.assert_not_called()

    def test_active_deployment_state_round_trips(self):
        with tempfile.TemporaryDirectory() as cedar_home, patch.object(Util, 'cedar_home', cedar_home):
            DockerWorker._record_active_deployment(DockerDeploymentMode.HYBRID)
            state_path = DockerWorker._deployment_state_path()
            with open(state_path, 'r', encoding='utf-8') as state_file:
                self.assertEqual(
                    {'mode': 'hybrid'},
                    json.load(state_file),
                )
            self.assertEqual(
                DockerDeploymentMode.HYBRID,
                DockerWorker.active_deployment(),
            )
            self.assertIsNone(DockerWorker.active_train())
            DockerWorker._clear_active_deployment()
            self.assertIsNone(DockerWorker.active_deployment())

    @patch.object(DockerWorker, '_docker_command')
    def test_running_compose_projects_returns_only_known_running_projects(self, command):
        command.return_value = type('Result', (), {
            'returncode': 0,
            'stdout': 'cedar-infrastructure\nunrelated\ncedar-microservices\n',
            'stderr': '',
        })()

        self.assertEqual(
            {'cedar-infrastructure', 'cedar-microservices'},
            DockerWorker.running_compose_projects(),
        )


if __name__ == '__main__':
    unittest.main()

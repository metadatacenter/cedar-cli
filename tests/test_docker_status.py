import json
import unittest
from io import StringIO
from unittest.mock import patch

from rich.console import Console

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.DockerWorker import DockerWorker


def container(service, state='running', health='healthy'):
    state_payload = {'Status': state}
    if health is not None:
        state_payload['Health'] = {'Status': health}
    return {
        'Name': f'/{service}',
        'Created': '2026-08-21T18:00:00Z',
        'Config': {'Labels': {'com.docker.compose.service': service}},
        'State': state_payload,
    }


class DockerStatusTest(unittest.TestCase):

    def setUp(self):
        self.console_patch = patch(
            'org.metadatacenter.worker.DockerWorker.console',
            Console(file=StringIO(), force_terminal=False),
        )
        self.console_patch.start()

    def tearDown(self):
        self.console_patch.stop()

    @patch.object(DockerWorker, '_compose_containers')
    @patch.object(DockerWorker, '_expected_compose_services')
    @patch.object(DockerWorker, '_docker_server_version', return_value=('29.6.2', None))
    @patch.object(Util, 'cedar_home', '/tmp/CEDAR')
    def test_core_status_is_green_without_optional_admin(self, _version, expected, actual):
        expected.return_value = (['one'], None)
        actual.return_value = ({'one': container('one')}, None)

        self.assertTrue(DockerWorker.status())
        self.assertEqual(3, expected.call_count)
        self.assertEqual(3, actual.call_count)
        checked_projects = [call.args[0] for call in actual.call_args_list]
        self.assertNotIn('cedar-admin', checked_projects)

    @patch.object(DockerWorker, '_compose_containers')
    @patch.object(DockerWorker, '_expected_compose_services')
    @patch.object(DockerWorker, '_docker_server_version', return_value=('29.6.2', None))
    @patch.object(Util, 'cedar_home', '/tmp/CEDAR')
    def test_admin_can_be_required_explicitly(self, _version, expected, actual):
        expected.return_value = (['one'], None)
        actual.return_value = ({'one': container('one')}, None)

        self.assertTrue(DockerWorker.status(include_admin=True))
        self.assertEqual(4, expected.call_count)
        self.assertIn('cedar-admin', [call.args[0] for call in actual.call_args_list])

    @patch.object(DockerWorker, '_compose_containers')
    @patch.object(DockerWorker, '_expected_compose_services')
    @patch.object(DockerWorker, '_docker_server_version', return_value=('29.6.2', None))
    @patch.object(Util, 'cedar_home', '/tmp/CEDAR')
    def test_frontends_can_be_excluded_for_hybrid_mode(self, _version, expected, actual):
        expected.return_value = (['one'], None)
        actual.return_value = ({'one': container('one')}, None)

        self.assertTrue(DockerWorker.status(include_frontends=False))
        self.assertEqual(2, expected.call_count)
        self.assertNotIn('cedar-frontend', [call.args[0] for call in actual.call_args_list])

    @patch.object(DockerWorker, '_compose_containers', return_value=({}, None))
    @patch.object(DockerWorker, '_expected_compose_services', return_value=(['missing'], None))
    @patch.object(DockerWorker, '_docker_server_version', return_value=('29.6.2', None))
    @patch.object(Util, 'cedar_home', '/tmp/CEDAR')
    def test_missing_required_container_fails(self, _version, _expected, _actual):
        self.assertFalse(DockerWorker.status())

    @patch.object(DockerWorker, '_expected_compose_services')
    @patch.object(DockerWorker, '_docker_server_version', return_value=(None, 'daemon unavailable'))
    def test_unavailable_daemon_fails_before_reading_compose(self, _version, expected):
        self.assertFalse(DockerWorker.status())
        expected.assert_not_called()

    def test_container_report_handles_health_and_runtime_state(self):
        self.assertEqual('✅', DockerWorker._container_report(container('ok'))[0])
        self.assertEqual('✅', DockerWorker._container_report(container('no-check', health=None))[0])
        self.assertEqual('⏳', DockerWorker._container_report(container('warming', health='starting'))[0])
        self.assertEqual('❌', DockerWorker._container_report(container('bad', health='unhealthy'))[0])
        self.assertEqual('❌', DockerWorker._container_report(container('stopped', state='exited'))[0])
        self.assertEqual('❌', DockerWorker._container_report(None)[0])

    @patch.object(DockerWorker, '_docker_command')
    def test_compose_container_inventory_uses_labels(self, command):
        inspected = [container('one'), container('two')]
        command.side_effect = [
            type('Result', (), {'returncode': 0, 'stdout': 'id1\nid2\n', 'stderr': ''})(),
            type('Result', (), {'returncode': 0, 'stdout': json.dumps(inspected), 'stderr': ''})(),
        ]

        containers, error = DockerWorker._compose_containers('cedar-frontend')

        self.assertIsNone(error)
        self.assertEqual({'one', 'two'}, set(containers))
        self.assertIn('label=com.docker.compose.project=cedar-frontend', command.call_args_list[0].args[0])


if __name__ == '__main__':
    unittest.main()

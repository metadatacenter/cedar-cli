import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import env
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.ModeManager import ModeManager
from org.metadatacenter.worker.DockerWorker import DockerWorker


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class EnvironmentCommandTest(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    @patch.object(ModeManager, 'profile_environment', return_value={
        'CEDAR_HOST': 'metadatacenter.orgx',
        'CEDAR_PUBLIC_VALUE': 'visible',
        'CEDAR_ADMIN_USER_API_KEY': 'must-not-appear',
        'CEDAR_DATABASE_PASSWORD': 'also-must-not-appear',
    })
    @patch.object(ModeManager, 'current', return_value=CedarMode.NATIVE)
    def test_list_uses_effective_profile_and_redacts_secrets(self, _current, profile):
        result = self.runner.invoke(env.app, ['list'])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('CEDAR_PUBLIC_VALUE', result.output)
        self.assertIn('visible', result.output)
        self.assertIn('<redacted>', result.output)
        self.assertNotIn('must-not-appear', result.output)
        self.assertNotIn('also-must-not-appear', result.output)
        profile.assert_called_once_with('native', CedarMode.NATIVE)

    @patch.object(ModeManager, 'current', return_value=CedarMode.HYBRID)
    def test_hybrid_list_requires_an_explicit_surface(self, _current):
        result = self.runner.invoke(env.app, ['list'])
        output = ' '.join(result.output.split())

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn('separate native and Docker environments', output)
        self.assertIn('choose native or docker', output)

    @patch.object(ModeManager, 'profile_environment', return_value={
        'CEDAR_IMAGE_PREFIX': 'registry.example/cedar',
    })
    @patch.object(ModeManager, 'current', return_value=CedarMode.HYBRID)
    def test_hybrid_can_inspect_the_docker_surface(self, _current, profile):
        result = self.runner.invoke(env.app, ['filter', 'IMAGE', 'docker'])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('CEDAR_IMAGE_PREFIX', result.output)
        profile.assert_called_once_with('docker', CedarMode.HYBRID)

    @patch.object(ModeManager, 'current', return_value=CedarMode.NATIVE)
    def test_inactive_surface_is_rejected(self, _current):
        result = self.runner.invoke(env.app, ['list', 'docker'])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn('the docker environment is not active', result.output)

    @patch.object(ModeManager, 'current', return_value=None)
    def test_list_requires_a_selected_mode(self, _current):
        result = self.runner.invoke(env.app, ['list'])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn('CEDAR mode is not set', result.output)

    def test_legacy_core_command_is_removed(self):
        result = self.runner.invoke(env.app, ['core'])

        self.assertEqual(2, result.exit_code, result.output)

    @patch.object(DockerWorker, 'active_train', return_value='2.9.3-dev.20260825.1700')
    @patch.object(ModeManager, 'cedar_home', return_value=Path('/tmp/CEDAR'))
    @patch.object(ModeManager, 'state_path', return_value=Path('/tmp/CEDAR/.cedar/mode.json'))
    @patch.object(ModeManager, 'profile_environment', side_effect=lambda surface, _mode: {
        'native': {
            'CEDAR_HOST': 'metadatacenter.orgx',
            'CEDAR_NET_GATEWAY': '127.0.0.1',
            'CEDAR_NET_SUBNET': '127.0.0.0',
        },
        'docker': {
            'CEDAR_HOST': 'metadatacenter.orgx',
            'CEDAR_NET_GATEWAY': '192.168.17.1',
            'CEDAR_NET_SUBNET': '192.168.17.0',
            'CEDAR_DOCKER_MODE': 'hybrid',
            'CEDAR_IMAGE_PREFIX': 'registry.example/cedar',
            'CEDAR_BASE_IMAGE_PREFIX': 'registry.example/internal',
        },
    }[surface])
    @patch.object(ModeManager, 'current', return_value=CedarMode.HYBRID)
    def test_status_describes_both_hybrid_surfaces(
            self, _current, profile, _state, _home, _train):
        result = self.runner.invoke(env.app, ['status'])
        output = ANSI_ESCAPE.sub('', result.output)

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('hybrid', output)
        self.assertIn('Native profile', output)
        self.assertIn('Docker profile', output)
        self.assertIn('registry.example/cedar', output)
        self.assertIn('2.9.3-dev.20260825.1700', output)
        self.assertEqual(2, profile.call_count)

    def test_bootstrap_does_not_preload_one_surface_for_env_commands(self):
        with patch.object(ModeManager, 'current') as current, \
                patch.object(ModeManager, 'profile_environment') as profile, \
                patch.dict(os.environ, {}, clear=True):
            ModeManager.bootstrap(['env', 'list', 'docker'])

        current.assert_not_called()
        profile.assert_not_called()


if __name__ == '__main__':
    unittest.main()

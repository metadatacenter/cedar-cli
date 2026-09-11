import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner
from org.metadatacenter import env, prod
from org.metadatacenter.util.ArtifactServiceKey import CURRENT, PREVIOUS, KEY
from org.metadatacenter.util.ModeManager import ModeManager
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.model.CedarProfile import CedarProfile
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context


class ArtifactServiceKeyTest(unittest.TestCase):
    def test_production_provisioning_reuses_private_key_without_restarting_services(self):
        with tempfile.TemporaryDirectory() as directory, \
                use_context(InvocationContext(environment={'CEDAR_HOME': directory})), \
                patch.object(ModeManager, 'current', return_value=CedarMode.NATIVE), \
                patch.object(ModeManager, 'current_profile', return_value=CedarProfile.SERVER), \
                patch('subprocess.Popen') as process:
            runner = CliRunner()
            path = Path(directory) / '.cedar/secrets/artifact-service.sh'
            first = runner.invoke(prod.app, ['provision-artifact-key'])
            self.assertEqual(0, first.exit_code, first.output)
            content = path.read_text()
            second = runner.invoke(prod.app, ['provision-artifact-key'])
            self.assertEqual(0, second.exit_code, second.output)
            self.assertEqual(content, path.read_text())
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
            for result in (first, second):
                self.assertIn('No copying or manual export', result.output)
                for line in content.splitlines():
                    if line.startswith(f'export {CURRENT}='):
                        self.assertNotIn(line.split('"')[1], result.output)
            process.assert_not_called()

    def test_production_provisioning_refuses_wrong_topology_and_external_secret(self):
        for mode, profile, supplied in [(CedarMode.NATIVE, CedarProfile.DEVELOP, ''),
                                         (CedarMode.DOCKER, CedarProfile.SERVER, ''),
                                         (CedarMode.NATIVE, CedarProfile.SERVER, 'external-secret')]:
            with self.subTest(mode=mode, profile=profile, supplied=bool(supplied)), \
                    tempfile.TemporaryDirectory() as directory, \
                    use_context(InvocationContext(environment={'CEDAR_HOME': directory, CURRENT: supplied})), \
                    patch.object(ModeManager, 'current', return_value=mode), \
                    patch.object(ModeManager, 'current_profile', return_value=profile):
                result = CliRunner().invoke(prod.app, ['provision-artifact-key'])
                self.assertEqual(1, result.exit_code, result.output)
                self.assertFalse((Path(directory) / '.cedar/secrets/artifact-service.sh').exists())
                if supplied:
                    self.assertNotIn(supplied, result.output)

    def test_native_processes_receive_only_the_credentials_they_need(self):
        controller = Path(__file__).resolve().parents[2] / 'cedar-development/ops/cedar-services.sh'
        with tempfile.TemporaryDirectory() as directory:
            environment = {'PATH': '/usr/bin:/bin', 'CEDAR_HOME': directory,
                           'CEDAR_SERVICES_INSPECT_ONLY': 'true', 'CEDAR_SERVICES_LIBRARY_ONLY': 'true',
                           CURRENT: 'test-current', PREVIOUS: 'test-previous'}
            for service, expected in [('artifact', 'test-current|test-previous'),
                                      ('resource', 'test-current|absent'), ('worker', 'test-current|absent'),
                                      ('bridge', 'absent|absent'), ('repo', 'absent|absent'),
                                      ('openview', 'absent|absent'), ('ui-main', 'absent|absent')]:
                with self.subTest(service=service):
                    result = subprocess.run(['bash', '-c',
                        'source "$1"; scope_artifact_service_credentials "$2"; '
                        'printf "%s|%s" "${CEDAR_ARTIFACT_SERVICE_API_KEY-absent}" '
                        '"${CEDAR_ARTIFACT_SERVICE_PREVIOUS_API_KEY-absent}"',
                        'scope-test', str(controller), service], env=environment, text=True, capture_output=True)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(expected, result.stdout)

    def test_private_initialization_and_overlapping_rotation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ModeManager, 'cedar_home', return_value=Path(directory)):
            runner = CliRunner()
            path = Path(directory) / '.cedar/secrets/artifact-service.sh'
            def run(action):
                result = runner.invoke(env.app, ['artifact-key', action])
                self.assertEqual(0, result.exit_code, result.output)
                values = dict(line.removeprefix('export ').split('=', 1) for line in path.read_text().splitlines()
                              if line.startswith('export '))
                values = {name: value.strip('"') for name, value in values.items()}
                self.assertNotIn(values[CURRENT], result.output)
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
                return values
            first = run('init')
            self.assertRegex(first[CURRENT], KEY)
            self.assertEqual('', first[PREVIOUS])
            self.assertEqual(first, run('init'))
            second = run('rotate')
            self.assertNotEqual(first[CURRENT], second[CURRENT])
            self.assertEqual(first[CURRENT], second[PREVIOUS])
            self.assertEqual(1, runner.invoke(env.app, ['artifact-key', 'rotate']).exit_code)
            retired = run('retire')
            self.assertEqual(second[CURRENT], retired[CURRENT])
            self.assertEqual('', retired[PREVIOUS])

    def test_refuses_to_follow_a_key_file_symlink(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ModeManager, 'cedar_home', return_value=Path(directory)):
            folder = Path(directory) / '.cedar/secrets'
            folder.mkdir(parents=True)
            target = Path(directory) / 'untouched'
            target.write_text('do not overwrite')
            (folder / 'artifact-service.sh').symlink_to(target)
            result = CliRunner().invoke(env.app, ['artifact-key', 'init'])
            self.assertEqual(1, result.exit_code)
            self.assertEqual('do not overwrite', target.read_text())

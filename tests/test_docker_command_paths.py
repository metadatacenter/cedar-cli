import unittest
from unittest.mock import patch

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.DockerWorker import DockerWorker
from org.metadatacenter.worker.Worker import CommandOutput


class DockerCommandPathsTest(unittest.TestCase):

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    @patch.object(Util, 'cedar_home', '/tmp/CEDAR')
    def test_start_uses_local_snapshot_pull_policy_and_current_stack(self, execute):
        execute.return_value = CommandOutput([], 0)

        self.assertEqual(0, DockerWorker.start_frontends(detach=True))

        command = execute.call_args.args[0][0]
        self.assertEqual('docker compose up -d --pull never', command)
        self.assertTrue(execute.call_args.kwargs['cwd'].endswith('cedar-docker-deploy/cedar-frontend'))

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    @patch.object(Util, 'cedar_home', '/tmp/CEDAR')
    def test_compose_failure_is_returned_to_the_cli(self, execute):
        execute.return_value = CommandOutput([], 17)

        self.assertEqual(17, DockerWorker.stop_microservices())

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    def test_validate_failure_is_returned_to_the_cli(self, execute):
        execute.return_value = CommandOutput([], 9)

        self.assertEqual(9, DockerWorker.validate())

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    def test_certificate_setup_creates_both_external_volumes(self, execute):
        execute.return_value = CommandOutput([], 0)

        DockerWorker.create_certificates_volume()

        command = execute.call_args.args[0][0]
        self.assertIn('docker volume create cedar_cert', command)
        self.assertIn('docker volume create cedar_ca', command)

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    def test_volume_removal_includes_both_new_frontends(self, execute):
        execute.return_value = CommandOutput([], 0)

        DockerWorker.remove_volumes()

        command = execute.call_args.args[0][0]
        self.assertIn('log_frontend_workspace', command)
        self.assertIn('log_frontend_template_designer', command)


if __name__ == '__main__':
    unittest.main()

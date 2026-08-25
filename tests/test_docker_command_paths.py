import unittest
from unittest.mock import patch

from org.metadatacenter.util.Util import Util
from org.metadatacenter.util.DockerImages import DockerImages
from org.metadatacenter.worker.DockerWorker import DockerWorker
from org.metadatacenter.worker.Worker import CommandOutput


class DockerCommandPathsTest(unittest.TestCase):

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    @patch.object(DockerImages, 'server_versions', return_value={
        'CEDAR_MAVEN_VERSION': '2.9.3-SNAPSHOT',
        'NGINX_VERSION': '1.2.3',
    })
    @patch.object(DockerImages, 'build_home', return_value='/tmp/CEDAR/cedar-docker-build')
    @patch.object(DockerImages, 'source_revision', return_value='a' * 40)
    @patch.object(DockerImages, 'base_image_prefix', return_value='example/internal')
    @patch.object(DockerImages, 'reference', return_value=(
        'example/cedar/cedar-infra-nginx:2.9.3-dev.20260824.1847'
    ))
    @patch.object(DockerImages, 'manifest', return_value=(
        ['cedar-infra-nginx'], '2.9.3-dev.20260824.1847', 'example/cedar',
    ))
    def test_train_build_tags_image_and_downloads_same_maven_version(
            self, _manifest, _reference, _base_prefix, _revision, _home, _versions, execute):
        execute.return_value = CommandOutput(['All requested images built.'], 0)

        self.assertEqual(0, DockerWorker.build_images(
            ['cedar-infra-nginx'],
            train='2.9.3-dev.20260824.1847',
        ))

        command = execute.call_args.args[0][0]
        self.assertIn('CEDAR_DOCKER_VERSION="2.9.3-dev.20260824.1847"', command)
        self.assertIn('CEDAR_MAVEN_VERSION="2.9.3-dev.20260824.1847"', command)
        self.assertIn('CEDAR_IMAGE_PREFIX="example/internal"', command)
        self.assertIn('org.metadatacenter.cedar.train="2.9.3-dev.20260824.1847"', command)
        self.assertIn('org.opencontainers.image.revision="' + 'a' * 40 + '"', command)
        self.assertIn('-t "example/cedar/cedar-infra-nginx:2.9.3-dev.20260824.1847"', command)

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
    def test_individual_component_commands_use_compose_service_names(self, execute):
        execute.return_value = CommandOutput([], 0)

        self.assertEqual(0, DockerWorker.start_frontend('designer', detach=True))
        self.assertEqual(
            'docker compose up -d --pull never frontend-template-designer',
            execute.call_args.args[0][0],
        )

        self.assertEqual(0, DockerWorker.stop_microservice('open'))
        self.assertEqual(
            'docker compose stop server-openview',
            execute.call_args.args[0][0],
        )

        self.assertEqual(0, DockerWorker.start_keycloak(detach=True))
        self.assertEqual(
            'docker compose up -d --pull never keycloak',
            execute.call_args.args[0][0],
        )

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    @patch.object(Util, 'cedar_home', '/tmp/CEDAR')
    def test_compose_failure_is_returned_to_the_cli(self, execute):
        execute.return_value = CommandOutput([], 17)

        self.assertEqual(17, DockerWorker.stop_microservices())

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    def test_validate_failure_is_returned_to_the_cli(self, execute):
        execute.return_value = CommandOutput([], 9)

        self.assertEqual(9, DockerWorker.validate())
        self.assertIn('CEDAR_IMAGE_PREFIX', execute.call_args.args[0][0])

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    def test_validate_rejects_malformed_image_prefix_before_compose(self, execute):
        self.assertEqual(1, DockerWorker.validate({'CEDAR_IMAGE_PREFIX': 'https://registry/cedar'}))
        execute.assert_not_called()

    @patch('org.metadatacenter.worker.DockerWorker.Worker.execute_generic_shell_commands')
    @patch.object(DockerImages, 'image_prefix', return_value='nexus.example.org:5000/cedar')
    def test_image_removal_uses_configured_prefix(self, _prefix, execute):
        execute.return_value = CommandOutput([], 0)

        self.assertEqual(0, DockerWorker.remove_images())

        command = execute.call_args.args[0][0]
        self.assertIn('nexus.example.org:5000/cedar/cedar-', command)

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

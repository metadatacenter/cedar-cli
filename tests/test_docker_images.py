import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import docker
from org.metadatacenter.docker_build import normalize_target
from org.metadatacenter.util.DockerImages import DockerImages


class DockerImagesTest(unittest.TestCase):

    def test_build_uses_shared_component_target_syntax(self):
        self.assertEqual("cedar-frontend-workspace", normalize_target("frontend", "workspace"))
        self.assertEqual("cedar-server-openview", normalize_target("microservice", "open"))
        self.assertEqual("frontends", normalize_target("frontend", "all"))
        self.assertEqual("microservices", normalize_target("microservice", "all"))

    def test_build_uses_keycloak_target_aliases(self):
        self.assertEqual("cedar-infra-keycloak", normalize_target("keycloak"))
        self.assertEqual("cedar-infra-keycloak", normalize_target("kk"))

    def test_build_rejects_invalid_component_target_combinations(self):
        with self.assertRaisesRegex(ValueError, '"infra" does not take a component name'):
            normalize_target("infra", "nginx")

    @patch('org.metadatacenter.docker_build.DockerWorker.build_images', return_value=0)
    @patch.object(DockerImages, 'with_dependencies', side_effect=lambda images: images)
    @patch.object(DockerImages, 'resolve', side_effect=lambda target: [target])
    def test_cli_build_accepts_shared_targets_and_images(self, resolve, _dependencies, _build):
        runner = CliRunner()

        for arguments, expected in (
                (["build", "frontend", "workspace", "--local"], "cedar-frontend-workspace"),
                (["build", "kk", "--local"], "cedar-infra-keycloak"),
                (["build", "cedar-server-artifact", "--local"], "cedar-server-artifact")):
            with self.subTest(arguments=arguments):
                result = runner.invoke(docker.app, arguments)
                self.assertEqual(0, result.exit_code, result.output)
                self.assertEqual(expected, resolve.call_args.args[0])

    @patch.object(DockerImages, 'image_prefix', return_value='metadatacenter')
    @patch.object(DockerImages, '_manifest_path', return_value=str(
        Path(__file__).resolve().parents[2] / 'cedar-docker-build' / 'bin' / 'cedar-images-base.sh'
    ))
    def test_manifest_uses_train_version_override(self, _path, _prefix):
        _images, version, _prefix = DockerImages.manifest({
            'CEDAR_TRAIN_VERSION': '2.9.3-dev.20260824.1847',
        })
        self.assertEqual('2.9.3-dev.20260824.1847', version)

    @patch.object(DockerImages, 'default_image_prefix', return_value='metadatacenter')
    def test_image_prefix_uses_environment_override(self, _default):
        environment = {'CEDAR_IMAGE_PREFIX': 'nexus.example.org:5000/team'}

        self.assertEqual(
            'nexus.example.org:5000/team',
            DockerImages.image_prefix(environment),
        )

    @patch.object(DockerImages, 'default_image_prefix', return_value='metadatacenter')
    def test_internal_bases_can_use_a_separate_registry(self, _default):
        environment = {
            'CEDAR_IMAGE_PREFIX': 'nexus.example.org/docker-cedar',
            'CEDAR_BASE_IMAGE_PREFIX': 'nexus.example.org/docker-cedar-internal',
        }

        self.assertEqual(
            'nexus.example.org/docker-cedar-internal/cedar-java:2.9.3-dev.20260824.1847',
            DockerImages.reference('cedar-java', '2.9.3-dev.20260824.1847', environment),
        )
        self.assertEqual(
            'nexus.example.org/docker-cedar/cedar-server-user:2.9.3-dev.20260824.1847',
            DockerImages.reference('cedar-server-user', '2.9.3-dev.20260824.1847', environment),
        )

    @patch.object(DockerImages, 'manifest', return_value=(
        ['cedar-admin-tool', 'cedar-java', 'cedar-server-user', 'cedar-frontend-main'],
        '2.9.3-SNAPSHOT',
        'metadatacenter',
    ))
    def test_core_is_not_a_build_target(self, _manifest):
        with self.assertRaisesRegex(ValueError, 'unknown target "core"'):
            DockerImages.resolve('core')

    @patch.object(DockerImages, 'manifest', return_value=(
        ['cedar-infra-nginx', 'cedar-java', 'cedar-frontend-main'],
        '2.9.3-SNAPSHOT',
        'metadatacenter',
    ))
    def test_infrastructure_build_group_is_named_infra(self, _manifest):
        self.assertEqual(['cedar-infra-nginx'], DockerImages.resolve('infra'))
        with self.assertRaisesRegex(ValueError, 'unknown target "infrastructure"'):
            DockerImages.resolve('infrastructure')

    @patch.object(DockerImages, 'manifest', return_value=(
        ['cedar-server-artifact', 'cedar-frontend-workspace'],
        '2.9.3-SNAPSHOT',
        'metadatacenter',
    ))
    def test_build_accepts_full_and_short_image_names(self, _manifest):
        self.assertEqual(
            ['cedar-server-artifact'],
            DockerImages.resolve('cedar-server-artifact'),
        )
        self.assertEqual(
            ['cedar-server-artifact'],
            DockerImages.resolve('artifact-server'),
        )

    def test_valid_image_prefixes(self):
        for prefix in (
                'metadatacenter',
                'team/project',
                'registry.example.org/team',
                'registry.example.org:5000/team/sub-project'):
            with self.subTest(prefix=prefix):
                self.assertEqual(prefix, DockerImages.validate_image_prefix(prefix))

    def test_invalid_image_prefixes(self):
        for prefix in (
                '',
                'https://registry.example.org/team',
                'Registry.Example.org/team',
                'registry.example.org/team/',
                'registry.example.org:latest/team',
                'registry.example.org:70000/team',
                'registry.example.org/team@sha256:deadbeef',
                'registry.example.org/bad team'):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    DockerImages.validate_image_prefix(prefix)


if __name__ == '__main__':
    unittest.main()

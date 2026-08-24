import unittest
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter.util.DockerImages import DockerImages


class DockerImagesTest(unittest.TestCase):

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
    def test_core_group_excludes_optional_admin_images(self, _manifest):
        self.assertEqual(
            ['cedar-java', 'cedar-server-user', 'cedar-frontend-main'],
            DockerImages.resolve('core'),
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

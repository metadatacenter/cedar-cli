import unittest
from unittest.mock import patch

from org.metadatacenter.util.DockerImages import DockerImages


class DockerImagesTest(unittest.TestCase):

    @patch.object(DockerImages, 'default_image_prefix', return_value='metadatacenter')
    def test_image_prefix_uses_environment_override(self, _default):
        environment = {'CEDAR_IMAGE_PREFIX': 'nexus.example.org:5000/team'}

        self.assertEqual(
            'nexus.example.org:5000/team',
            DockerImages.image_prefix(environment),
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

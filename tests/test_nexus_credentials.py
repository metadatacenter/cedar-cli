import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from org.metadatacenter.util.NexusCredentials import CredentialError, environment_with_nexus_credentials


class NexusCredentialsTest(unittest.TestCase):
    def test_explicit_empty_environment_does_not_read_the_users_settings(self):
        self.assertEqual({}, environment_with_nexus_credentials({}))

    def test_partial_credentials_fill_only_missing_values_without_mutating_input(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / '.m2/settings.xml'
            settings.parent.mkdir()
            settings.write_text('<settings><servers><server><id>bmir-nexus-releases</id>'
                                '<username>stored</username><password>secret</password>'
                                '</server></servers></settings>')
            original = {'HOME': root, 'BMIR_NEXUS_USERNAME': 'explicit'}
            result = environment_with_nexus_credentials(original)
            self.assertEqual('explicit', result['BMIR_NEXUS_USERNAME'])
            self.assertEqual('secret', result['BMIR_NEXUS_PASSWORD'])
            self.assertNotIn('BMIR_NEXUS_PASSWORD', original)
            settings.write_text('<invalid')
            with self.assertRaises(CredentialError):
                environment_with_nexus_credentials(original)

    def test_build_train_import_does_not_load_release_commands(self):
        result = subprocess.run([sys.executable, '-c',
            'import sys; from org.metadatacenter.worker.BuildTrainWorker import BuildTrainWorker; '
            'assert "org.metadatacenter.release_train" not in sys.modules'], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

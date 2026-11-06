from pathlib import Path
import tempfile
import unittest

from org.metadatacenter.release_support.readiness import docker_default_findings
from org.metadatacenter.release_support.versioning import ReleaseVersionPreparer


class DockerCompatibilityDefaultsTest(unittest.TestCase):
    def test_readiness_rejects_expiring_defaults_before_a_train(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'cedar-docker-build/bin/cedar-images-base.sh'
            base.parent.mkdir(parents=True)
            base.write_text('export CEDAR_WORKSPACE_NPM_VERSION=2.9.32-dev.20261106.1722\n'
                            'export CEDAR_NPM_REGISTRY=https://nexus.bmir.stanford.edu/repository/npm-cedar\n')
            findings = docker_default_findings(root)
            self.assertEqual(2, len(findings))
            self.assertIn('CEDAR_WORKSPACE_NPM_VERSION', findings[0])
            base.write_text('export CEDAR_WORKSPACE_NPM_VERSION=2.9.32\n'
                            'export CEDAR_NPM_REGISTRY=https://nexus.bmir.stanford.edu/repository/npm-cedar-releases\n')
            self.assertEqual([], docker_default_findings(root))

    def test_next_development_preserves_available_retained_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'bin/cedar-images-base.sh'
            base.parent.mkdir()
            base.write_text(''.join(f'export {key}=2.9.33-SNAPSHOT\n' for key in
                                   ('IMAGE_VERSION', 'CEDAR_MAVEN_VERSION', 'CEDAR_APPLICATION_VERSION'))
                            + 'export CEDAR_WORKSPACE_NPM_VERSION=2.9.32\n'
                            + 'export CEDAR_OPENVIEW_CEE_NPM_VERSION=2.0.32\n'
                            + 'export CEDAR_NPM_REGISTRY=https://nexus.bmir.stanford.edu/repository/npm-cedar-releases\n')
            ReleaseVersionPreparer._stamp_docker_build(root, '2.9.33-SNAPSHOT', '2.9.34-SNAPSHOT', {})
            text = base.read_text()
            self.assertIn('CEDAR_APPLICATION_VERSION=2.9.34-SNAPSHOT', text)
            self.assertIn('CEDAR_WORKSPACE_NPM_VERSION=2.9.32', text)
            self.assertIn('CEDAR_OPENVIEW_CEE_NPM_VERSION=2.0.32', text)
            self.assertIn('/npm-cedar-releases\n', text)

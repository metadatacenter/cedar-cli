import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util import McpPins
from org.metadatacenter.worker.VersionWorker import VersionWorker

POM = '''<project xmlns="http://maven.apache.org/POM/4.0.0">
  <properties><cedar-artifact-library.version>{version}</cedar-artifact-library.version></properties>
  <dependencies>
    <dependency><groupId>org.metadatacenter</groupId><artifactId>cedar-model-library</artifactId>
      <version>1.0.0</version></dependency>
    <dependency><groupId>org.metadatacenter</groupId><artifactId>cedar-artifact-library</artifactId>
      <version>${{cedar-artifact-library.version}}</version></dependency>
  </dependencies>
</project>
'''


class McpPinTest(unittest.TestCase):
    """MCP servers' artifact library pins, against cedar-parent's newest release tag."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        parent = self.home / 'cedar-parent'
        parent.mkdir()
        for arguments in (['init', '-q'], ['config', 'user.email', 't@example.org'],
                          ['config', 'user.name', 'T'], ['commit', '-q', '--allow-empty', '-m', 'x']):
            subprocess.run(['git', '-C', str(parent), *arguments], check=True)
        # 2.9.36 is the newest release although 2.9.4 sorts after it as text.
        for tag in ('release-2.9.4', 'release-2.9.36', 'release-2.9.35', 'not-a-release'):
            subprocess.run(['git', '-C', str(parent), 'tag', tag], check=True)

    def server(self, name, version):
        pom = self.home / 'mcp' / name / 'pom.xml'
        pom.parent.mkdir(parents=True, exist_ok=True)
        pom.write_text(POM.format(version=version) if version else
                       '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies/></project>')

    def test_a_pin_older_than_the_last_release_is_named(self):
        self.server('cedar-artifact-mcp', '2.9.31')
        self.server('cedar-cee-mcp', '2.9.36')
        self.server('cedar-artifact-rest-mcp', '2.9.37-SNAPSHOT')
        self.server('bioportal-term-mcp', None)

        self.assertEqual((2, 9, 36), McpPins.last_release(self.home))
        self.assertEqual(["mcp/cedar-artifact-mcp pins cedar-artifact-library 2.9.31, older than the "
                          "platform's release 2.9.36"], McpPins.stale_pins(self.home))

    def test_pins_at_or_after_the_release_pass(self):
        self.server('cedar-artifact-mcp', '2.9.36')
        self.server('cedar-cee-mcp', '2.10.0')

        self.assertEqual([], McpPins.stale_pins(self.home))

    @patch('org.metadatacenter.worker.VersionWorker.Util.write_rich_cedar_file')
    @patch('org.metadatacenter.worker.VersionWorker.GlobalContext.repos')
    @patch('org.metadatacenter.worker.VersionWorker.console')
    def test_check_versions_fails_on_a_stale_mcp_pin(self, console, repos, _write):
        repo = Repo('matching', RepoType.MISC, [])
        repos.get_list_all.return_value = [repo]
        worker = VersionWorker()
        self.server('cedar-cee-mcp', '2.9.31')

        def add_version(repo, report):
            report.add(repo, f'/{repo.name}', 'version', 'test', '2.9.37-SNAPSHOT')

        with patch.object(worker, 'get_version_report', side_effect=add_version), \
                patch('org.metadatacenter.worker.VersionWorker.Util.cedar_home', str(self.home)):
            self.assertEqual(1, worker.check_versions())
            self.server('cedar-cee-mcp', '2.9.36')
            self.assertEqual(0, worker.check_versions())
        printed = ' '.join(str(call.args[0]) for call in console.print.call_args_list)
        self.assertIn('mcp/cedar-cee-mcp pins cedar-artifact-library 2.9.31', printed)


if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import check
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.VersionReport import VersionReport
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.RepoWorker import RepoWorker
from org.metadatacenter.worker.VersionWorker import VersionWorker


class CheckCommandsTest(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    @patch("org.metadatacenter.worker.RepoWorker.GlobalContext.repos")
    @patch("org.metadatacenter.worker.RepoWorker.console")
    def test_repo_check_ignores_workspace_noise_and_fails_for_missing_configured_repo(
            self, repo_console, repos):
        with tempfile.TemporaryDirectory() as temp_dir:
            present = Repo("present", RepoType.MISC, [])
            missing = Repo("missing", RepoType.MISC, [])
            repos.get_list_all.return_value = [present, missing]
            Path(temp_dir, "present", ".git").mkdir(parents=True)
            Path(temp_dir, "unmanaged", ".git").mkdir(parents=True)
            Path(temp_dir, "CEDAR_CA").mkdir()
            Path(temp_dir, "notes.txt").write_text("workspace noise")

            with patch.object(Util, "cedar_home", temp_dir):
                returncode = RepoWorker.check_repos()

        self.assertEqual(1, returncode)
        table = repo_console.print.call_args.args[0]
        rendered_cells = [str(cell) for column in table.columns for cell in column.cells]
        self.assertIn("unmanaged", rendered_cells)
        self.assertIn("unmanaged clone", rendered_cells)
        self.assertNotIn("CEDAR_CA", rendered_cells)
        self.assertNotIn("notes.txt", rendered_cells)

    @patch("org.metadatacenter.check.RepoWorker.check_repos", return_value=3)
    def test_repo_command_propagates_failure(self, check_repos):
        result = self.runner.invoke(check.app, ["repos"])

        self.assertEqual(3, result.exit_code)
        check_repos.assert_called_once_with()

    @patch("org.metadatacenter.check.version_worker.check_versions", return_value=4)
    def test_version_command_propagates_failure(self, check_versions):
        result = self.runner.invoke(check.app, ["versions"])

        self.assertEqual(4, result.exit_code)
        check_versions.assert_called_once_with()

    def test_docker_build_ignores_train_supplied_maven_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Repo("cedar-docker-build", RepoType.DOCKER_BUILD, [])
            docker_root = Path(temp_dir, repo.name)
            Path(docker_root, "cedar-microservice").mkdir(parents=True)
            Path(docker_root, "cedar-microservice", "Dockerfile").write_text(
                "ENV CEDAR_VERSION=${CEDAR_MAVEN_VERSION}\n"
            )
            Path(docker_root, "bin").mkdir()
            Path(docker_root, "bin", "cedar-images-base.sh").write_text(
                'export IMAGE_VERSION="2.9.3-SNAPSHOT"\n'
            )
            report = VersionReport()

            with patch.object(Util, "cedar_home", temp_dir):
                VersionWorker.analyze_docker_build(repo, report)

        versions = [entry.version for entry in report.entries]
        self.assertNotIn("${CEDAR_MAVEN_VERSION}", versions)
        self.assertTrue(any("2.9.3-SNAPSHOT" in version for version in versions))

    @patch("org.metadatacenter.worker.VersionWorker.Util.write_rich_cedar_file")
    @patch("org.metadatacenter.worker.VersionWorker.GlobalContext.repos")
    @patch("org.metadatacenter.worker.VersionWorker.console")
    def test_version_check_fails_for_real_mismatch(self, version_console, repos, write_report):
        matching = Repo("matching", RepoType.MISC, [])
        mismatched = Repo("mismatched", RepoType.MISC, [])
        repos.get_list_all.return_value = [matching, mismatched]
        worker = VersionWorker()

        def add_version(repo, report):
            version = "2.9.3-SNAPSHOT" if repo is matching else "9.9.9"
            report.add(repo, f"/{repo.name}", "version", "test", version)

        with patch.object(worker, "get_version_report", side_effect=add_version):
            returncode = worker.check_versions()

        self.assertEqual(1, returncode)
        version_console.print.assert_called_once()
        write_report.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.VersionReport import VersionReport
from org.metadatacenter.model.VersionType import VersionType
from org.metadatacenter.worker.GitWorker import GitWorker
from org.metadatacenter.worker.VersionWorker import VersionWorker


@patch.dict("os.environ", {"CEDAR_HOME": "/tmp/CEDAR"})
class MissingCheckoutReportTest(unittest.TestCase):
    """
    A registered repository that is not on disk is reported, not raised.

    Three consecutive releases registered a new repository, and each one left every deployment host
    with an unhandled FileNotFoundError from the version check instead of an answer. The estate on
    disk cannot exercise this: once someone clones the repository, the case is gone.
    """

    def report_for_absent_repo(self, repo_type):
        repo = Repo("cedar-newly-registered", repo_type, [VersionType.PACKAGE_OWN])
        report = VersionReport()
        with patch("org.metadatacenter.worker.VersionWorker.RepoWorker.get_repo_dir_status",
                   return_value=False):
            VersionWorker().get_version_report(repo, report)
        return report

    def test_an_absent_repo_is_reported_rather_than_crashing(self):
        for repo_type in (RepoType.ANGULAR, RepoType.ANGULAR_JS, RepoType.TYPESCRIPT,
                          RepoType.JAVA, RepoType.ANGULAR_DIST):
            with self.subTest(repo_type=repo_type):
                report = self.report_for_absent_repo(repo_type)
                self.assertEqual(1, len(report.entries))
                self.assertEqual(VersionType.MISSING, report.entries[0].version_type)

    def test_an_absent_repo_fails_the_check_and_is_not_merely_unknown(self):
        report = self.report_for_absent_repo(RepoType.ANGULAR)
        report.summarize()
        self.assertEqual(1, report.cnt_nok)
        self.assertEqual(0, report.cnt_unknown)
        self.assertEqual(0, report.cnt_ok)

    def test_the_remedy_names_the_repository_and_the_command(self):
        report = self.report_for_absent_repo(RepoType.ANGULAR)
        report.summarize()
        lines = " ".join(report.get_remedy_lines())
        self.assertIn("cedar-newly-registered", lines)
        self.assertIn("cedarcli git clone-missing", lines)

    def test_a_missing_repo_is_not_also_reported_as_a_divergence(self):
        """Two remedies for one cause would send the operator to `git pull`, which cannot help."""
        report = self.report_for_absent_repo(RepoType.ANGULAR)
        report.summarize()
        lines = report.get_remedy_lines()
        self.assertEqual(1, len(lines))
        self.assertNotIn("differ from the target", lines[0])


@patch.dict("os.environ", {"CEDAR_HOME": "/tmp/CEDAR"})
class CloneMissingTest(unittest.TestCase):
    """`git clone-missing` clones exactly the absent top-level repositories, and nothing else."""

    @staticmethod
    def run_clone(present_names, top_repos, sub_repos=()):
        worker = GitWorker()
        repos = [Repo(name, RepoType.MISC, []) for name in top_repos]
        captured = {}

        def executor(**kwargs):
            captured.update(kwargs)
            return type("Result", (), {"returncode": 0})()

        with patch("org.metadatacenter.worker.GitWorker.GlobalContext.repos") as global_repos, \
                patch("org.metadatacenter.worker.GitWorker.RepoWorker.get_repo_dir_status",
                      side_effect=lambda repo: repo.name in present_names), \
                patch("org.metadatacenter.worker.GitWorker.console") as console, \
                patch.object(worker, "execute_shell_on_all_repos_with_table", side_effect=executor):
            global_repos.get_list_top.return_value = repos
            result = worker.clone_missing()
        printed = " ".join(str(call.args[0]) for call in console.print.call_args_list)
        return result, captured, printed

    def test_only_the_absent_repositories_are_cloned(self):
        _, captured, printed = self.run_clone(
            present_names={"cedar-present"},
            top_repos=["cedar-present", "cedar-absent"])
        self.assertEqual(["cedar-absent"], [repo.name for repo in captured["repo_list"]])
        self.assertIn("cedar-absent", printed)

    def test_the_clone_runs_from_the_workspace_root_and_moves_to_main(self):
        _, captured, _ = self.run_clone(present_names=set(), top_repos=["cedar-absent"])
        self.assertTrue(captured["cwd_is_home"])
        commands = "\n".join(captured["command_list"])
        self.assertIn("git clone https://github.com/metadatacenter/{0}", commands)
        self.assertIn("refs/remotes/origin/main", commands)
        self.assertIn("checkout main", commands)

    def test_a_remote_without_main_is_reported_rather_than_failing_the_clone(self):
        _, captured, _ = self.run_clone(present_names=set(), top_repos=["cedar-absent"])
        commands = "\n".join(captured["command_list"])
        self.assertIn("no origin/main", commands)

    def test_nothing_missing_is_a_successful_no_op(self):
        result, captured, printed = self.run_clone(
            present_names={"cedar-present"}, top_repos=["cedar-present"])
        self.assertEqual(0, result.returncode)
        self.assertEqual({}, captured)
        self.assertIn("nothing to clone", printed)


if __name__ == "__main__":
    unittest.main()

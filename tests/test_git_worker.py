import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import git as git_commands
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.GitWorker import GitWorker


class GitWorkerSafeCommitTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = Repo("sample", RepoType.MISC, [])
        self.repo_root = self.root / self.repo.name
        self.remote = self.root / "remote.git"
        self._git("init", "--bare", str(self.remote), cwd=self.root)
        self._git("init", "--initial-branch=develop", str(self.repo_root), cwd=self.root)
        self._git("config", "user.name", "Test User")
        self._git("config", "user.email", "test@example.org")
        (self.repo_root / "allowed.txt").write_text("initial\n")
        (self.repo_root / "outside.txt").write_text("initial\n")
        self._git("add", "--", "allowed.txt", "outside.txt")
        self._git("commit", "-m", "Initial")
        self._git("remote", "add", "origin", str(self.remote))
        self._git("push", "--set-upstream", "origin", "develop")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_comment_is_passed_literally_and_only_explicit_path_is_staged(self):
        comment = "O'Brien {safe}; $(touch injected)"
        (self.repo_root / "allowed.txt").write_text("updated\n")

        with patch.object(Util, "cedar_home", str(self.root)), \
                patch("org.metadatacenter.worker.GitWorker.GlobalContext.repos.get_list_all",
                      return_value=[self.repo]):
            result = GitWorker().git_add_commit_push(comment, self.repo.name, ["allowed.txt"])

        self.assertEqual("", result.results[0].err)
        self.assertEqual(comment, self._git("log", "-1", "--format=%s").stdout.strip())
        self.assertFalse((self.repo_root / "injected").exists())
        remote_subject = self._git(
            "--git-dir", str(self.remote), "log", "-1", "--format=%s", "refs/heads/develop", cwd=self.root
        )
        self.assertEqual(comment, remote_subject.stdout.strip())

    def test_refuses_to_stage_when_another_path_is_dirty(self):
        (self.repo_root / "allowed.txt").write_text("updated\n")
        (self.repo_root / "outside.txt").write_text("also updated\n")

        with patch.object(Util, "cedar_home", str(self.root)), \
                patch("org.metadatacenter.worker.GitWorker.GlobalContext.repos.get_list_all",
                      return_value=[self.repo]):
            result = GitWorker().git_add_commit_push("Safe", self.repo.name, ["allowed.txt"])

        self.assertIn("outside.txt", result.results[0].err)
        staged = self._git("diff", "--cached", "--name-only").stdout.strip()
        self.assertEqual("", staged)
        self.assertEqual("Initial", self._git("log", "-1", "--format=%s").stdout.strip())

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )


class GitCommandContractTest(unittest.TestCase):

    @patch("org.metadatacenter.git.git_worker.git_add_commit_push")
    def test_command_requires_repo_and_repeated_explicit_paths(self, add_commit_push):
        add_commit_push.return_value.results = []
        runner = CliRunner()

        result = runner.invoke(git_commands.app, [
            "add-commit-push", "message", "--repo", "sample",
            "--path", "one.txt", "--path", "dir/two.txt",
        ])

        self.assertEqual(0, result.exit_code, result.output)
        add_commit_push.assert_called_once_with("message", "sample", ["one.txt", "dir/two.txt"])


if __name__ == "__main__":
    unittest.main()

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner
from org.metadatacenter import git as git_commands
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.GitWorker import GitWorker


class GitFailureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repos = [Repo(name, RepoType.MISC, []) for name in ('first', 'second')]
        for repo in self.repos:
            subprocess.run(['git', 'init', '-q', str(self.root / repo.name)], check=True)
        self.enterContext(patch.object(Util, 'cedar_home', str(self.root)))
        self.enterContext(patch.object(GlobalContext.repos, 'get_list_top', return_value=self.repos))

    def test_failed_checkout_pull_and_fetch_propagate_to_cli(self):
        for args in (['checkout', 'missing-branch'], ['pull']):
            result = CliRunner().invoke(git_commands.app, args)
            self.assertEqual(1, result.exit_code, result.output)
            self.assertIn('first', result.output)
            self.assertIn('second', result.output)
        for repo in self.repos:
            subprocess.run(['git', '-C', str(self.root / repo.name), 'remote', 'add',
                            'origin', str(self.root / 'missing-remote')], check=True)
        result = CliRunner().invoke(git_commands.app, ['fetch'])
        self.assertEqual(1, result.exit_code, result.output)

    def test_silent_failure_keeps_exit_code_and_continues_other_repositories(self):
        result = GitWorker.execute_shell_on_all_repos_with_table(['exit 7'])
        self.assertEqual([7, 7], [row.returncode for row in result.results])
        self.assertEqual(1, result.returncode)
        self.assertTrue(all('7' in row.err for row in result.results))

    def test_success_keeps_zero_status(self):
        result = CliRunner().invoke(git_commands.app, ['status'])
        self.assertEqual(0, result.exit_code, result.output)

    def test_failed_status_does_not_update_next_repository_pointer(self):
        with patch.object(Util, 'get_wd', return_value=str(self.root / 'missing')), \
                patch.object(Util, 'write_cedar_file') as write:
            result = CliRunner().invoke(git_commands.app, ['next'])
        self.assertEqual(1, result.exit_code, result.output)
        write.assert_not_called()

    def test_failure_before_last_command_is_preserved(self):
        result = GitWorker.execute_shell_on_all_repos_with_table(['false\nprintf hidden'])
        self.assertEqual(1, result.returncode)
        self.assertTrue(all('hidden' not in row.out for row in result.results))

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner
from org.metadatacenter import git as commands
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util


class LiteralCheckoutTest(unittest.TestCase):
    def test_shell_metacharacters_are_literal_branch_names(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            repo = Repo('sample', RepoType.MISC, [])
            cwd = root / 'sample'
            def git(*args):
                return subprocess.run(['git', '-C', str(cwd), *args], check=True,
                                      text=True, capture_output=True).stdout.strip()
            subprocess.run(['git', 'init', '-q', str(cwd)], check=True)
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                'commit', '--allow-empty', '-m', 'Initial')
            branches = ['topic;uname', 'topic$(uname)', 'topic`uname`', "topic'quote", 'topic{brace}']
            with patch.object(Util, 'cedar_home', str(root)), \
                    patch.object(GlobalContext.repos, 'get_list_top', return_value=[repo]):
                for branch in branches:
                    git('branch', branch)
                    result = CliRunner().invoke(commands.app, ['checkout', branch])
                    self.assertEqual(0, result.exit_code, result.output)
                    self.assertEqual(branch, git('symbolic-ref', '--short', 'HEAD'))
                original = git('symbolic-ref', '--short', 'HEAD')
                result = CliRunner().invoke(commands.app, ['checkout', 'missing;touch injected'])
                self.assertEqual(1, result.exit_code, result.output)
                self.assertFalse((cwd / 'injected').exists())
                result = CliRunner().invoke(commands.app, ['checkout', '--', '--detach'])
                self.assertEqual(1, result.exit_code, result.output)
                self.assertEqual(original, git('symbolic-ref', '--short', 'HEAD'))
                # A missing revision must never become a checkout of a tracked path.
                tracked = cwd / 'tracked.txt'
                tracked.write_text('original')
                git('add', 'tracked.txt')
                git('-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-m', 'Tracked path')
                tracked.write_text('local edit')
                result = CliRunner().invoke(commands.app, ['checkout', 'tracked.txt'])
                self.assertEqual(1, result.exit_code, result.output)
                self.assertEqual('local edit', tracked.read_text())

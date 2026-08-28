import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CliWrapperTest(unittest.TestCase):
    def make_cli3_fixture(self, directory, return_code, with_next_git=False):
        cedar_home = Path(directory)
        cli = cedar_home / 'cedar-cli'
        activate = cli / '.venv' / 'bin' / 'activate'
        activate.parent.mkdir(parents=True)
        activate.write_text('', encoding='utf-8')
        (cli / 'cedar.py').write_text(
            f'import sys\nsys.exit({return_code})\n',
            encoding='utf-8',
        )

        next_git_file = cedar_home / 'next_git_repo'
        wrapper = (ROOT / 'cli3.sh').read_text(encoding='utf-8')
        wrapper = wrapper.replace(
            'NEXT_GIT_FILE=$HOME/.cedar/next_git_repo',
            f'NEXT_GIT_FILE={next_git_file}',
        )
        script = cli / 'cli3.sh'
        script.write_text(wrapper, encoding='utf-8')

        if with_next_git:
            destination = cedar_home / 'next'
            destination.mkdir()
            next_git_file.write_text(str(destination), encoding='utf-8')
        return cedar_home, script

    def run_wrapper(self, cedar_home, script, sourced):
        environment = dict(os.environ)
        environment['CEDAR_HOME'] = str(cedar_home)
        if sourced:
            command = ['bash', '-c', 'source "$1" status; exit $?', 'test', str(script)]
        else:
            command = ['bash', str(script), 'status']
        return subprocess.run(command, env=environment, check=False).returncode

    def test_cli3_propagates_python_failure_when_sourced(self):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, script = self.make_cli3_fixture(directory, 37)
            self.assertEqual(37, self.run_wrapper(cedar_home, script, sourced=True))

    def test_cli3_propagates_python_failure_when_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, script = self.make_cli3_fixture(directory, 37)
            self.assertEqual(37, self.run_wrapper(cedar_home, script, sourced=False))

    def test_cli3_navigation_does_not_replace_python_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, script = self.make_cli3_fixture(directory, 37, with_next_git=True)
            self.assertEqual(37, self.run_wrapper(cedar_home, script, sourced=True))

    def test_cli3_preserves_success(self):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, script = self.make_cli3_fixture(directory, 0)
            self.assertEqual(0, self.run_wrapper(cedar_home, script, sourced=True))


if __name__ == '__main__':
    unittest.main()

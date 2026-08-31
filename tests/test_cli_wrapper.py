"""
The shell wrapper's exit status is the CLI's exit status.

`cedar.py` exits non-zero when a plan fails, and for a long time none of that
reached the caller: the wrapper's status is that of its last command, which was
`popd` or the next-git `if`, and both succeed. A failed `build java` printed
"Execution halted because of an error!" and returned 0, so anything reading `$?`
— a script, a CI step, a `&&` chain — saw a green build.

There used to be two wrappers to get this wrong in. `cli3.sh` was a copy of
`cli.sh` naming `python3` where the original named `python`, and the capture
below was added to one of them three weeks before the other, because a test
covered only the copy no alias pointed at. `cli.sh` names `python3` now and the
copy is gone.

The fake venv provides `python3` on `PATH` the way a real one does, so the
wrapper under test resolves an interpreter without depending on what the machine
running the suite happens to offer outside a virtualenv.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

WRAPPER = 'cli.sh'


class CliWrapperTest(unittest.TestCase):
    def make_fixture(self, directory, return_code, with_next_git=False):
        cedar_home = Path(directory)
        cli = cedar_home / 'cedar-cli'
        bin_dir = cli / '.venv' / 'bin'
        bin_dir.mkdir(parents=True)

        shim = bin_dir / 'python3'
        shim.write_text(f'#!/bin/bash\nexec "{sys.executable}" "$@"\n', encoding='utf-8')
        shim.chmod(0o755)
        (bin_dir / 'activate').write_text(
            f'export PATH="{bin_dir}:$PATH"\n', encoding='utf-8')

        (cli / 'cedar.py').write_text(
            f'import sys\nsys.exit({return_code})\n',
            encoding='utf-8',
        )

        next_git_file = cedar_home / 'next_git_repo'
        wrapper = (ROOT / WRAPPER).read_text(encoding='utf-8')
        wrapper = wrapper.replace(
            'NEXT_GIT_FILE=$HOME/.cedar/next_git_repo',
            f'NEXT_GIT_FILE={next_git_file}',
        )
        script = cli / WRAPPER
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

    def assert_status(self, exits, expected, sourced=True, with_next_git=False):
        with tempfile.TemporaryDirectory() as directory:
            cedar_home, script = self.make_fixture(
                directory, exits, with_next_git=with_next_git)
            self.assertEqual(expected, self.run_wrapper(cedar_home, script, sourced))

    def test_propagates_python_failure_when_sourced(self):
        """Sourced is the normal path: the `cedarcli` alias sources the wrapper."""
        self.assert_status(exits=37, expected=37)

    def test_propagates_python_failure_when_executed(self):
        self.assert_status(exits=37, expected=37, sourced=False)

    def test_navigation_does_not_replace_python_failure(self):
        """`cd` into the next git repo succeeds, and used to be the status reported."""
        self.assert_status(exits=37, expected=37, with_next_git=True)

    def test_preserves_success(self):
        self.assert_status(exits=0, expected=0)

    def test_only_one_wrapper_exists(self):
        """
        The duplicate is what let the exit-status fix land in one file and not the
        other. A second wrapper is not a thing to add back.
        """
        self.assertEqual([WRAPPER], sorted(p.name for p in ROOT.glob('cli*.sh')))


if __name__ == '__main__':
    unittest.main()

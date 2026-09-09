"""Release ownership is an OS process boundary, not just an atomic JSON write."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from typer.testing import CliRunner
from org.metadatacenter import release_train
from org.metadatacenter.release_train import ReleaseError, ReleaseState


class ReleaseLockTest(unittest.TestCase):
    def test_competing_commands_refuse_before_work_but_status_can_read(self):
        with tempfile.TemporaryDirectory() as root:
            state = ReleaseState(root=Path(root))
            state.start({'releaseVersion': '1.0.0'})
            with state.exclusive(), patch.dict(os.environ, CEDAR_RELEASE_STATE_DIR=root), \
                    patch.object(release_train, '_activate_toolchain') as activate, \
                    patch.object(release_train, '_drive_release') as drive, \
                    patch.object(release_train, 'abandon_active_release') as abandon:
                for args in (
                    ['start', '--version', '1.0.0', '--next-version', '1.0.1-SNAPSHOT',
                     '--from-train', '1.0.0-dev.20260909.1200', '--cee-version', '2.0.0'],
                    ['resume'], ['abandon', '--version', '1.0.0', '--reason', 'test'],
                ):
                    result = CliRunner().invoke(release_train.app, args)
                    self.assertEqual(1, result.exit_code, result.output)
                    self.assertIn('Another release command is running', result.output)
                self.assertEqual('1.0.0', ReleaseState(root=Path(root)).read_current_manifest()[0]['releaseVersion'])
                activate.assert_not_called()
                drive.assert_not_called()
                abandon.assert_not_called()

    def test_process_exit_releases_lock_without_deleting_lock_file(self):
        with tempfile.TemporaryDirectory() as root:
            code = '''
import sys
from pathlib import Path
from org.metadatacenter.release_train import ReleaseState
with ReleaseState(root=Path(sys.argv[1])).exclusive():
    print('locked', flush=True)
    sys.stdin.read()
'''
            process = subprocess.Popen([sys.executable, '-c', code, root],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual('locked', process.stdout.readline().strip())
                state = ReleaseState(root=Path(root))
                with self.assertRaises(ReleaseError):
                    with state.exclusive():
                        self.fail('competing process acquired ownership')
                process.kill()
                process.wait(timeout=5)
                with state.exclusive():
                    self.assertTrue((Path(root) / 'release.lock').is_file())
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    def test_exception_releases_ownership(self):
        with tempfile.TemporaryDirectory() as root:
            state = ReleaseState(root=Path(root))
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                with state.exclusive():
                    raise RuntimeError('failed')
            with ReleaseState(root=Path(root)).exclusive():
                pass

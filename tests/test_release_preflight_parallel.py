import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter.release_support.preflight import ReleasePreflight


class ParallelPreflightTest(unittest.TestCase):
    def test_remote_probes_are_bounded_ordered_and_timeouts_fail_closed(self):
        for check in ('check_target_version_unused', 'check_push_permission'):
            with self.subTest(check=check), tempfile.TemporaryDirectory() as directory:
                repos = [f'repo-{n}' for n in range(8)]
                for repo in repos:
                    (Path(directory) / repo).mkdir()
                manifest = dict(releaseVersion='2.9.19', nextDevelopmentVersion='2.9.20-SNAPSHOT',
                                releaseRepositories=repos, sourceRepositories={r: 'a'*40 for r in repos})
                barrier = threading.Barrier(4)
                def run(command, **kwargs):
                    self.assertEqual(60, kwargs['timeout'])
                    if 'push' in command:
                        self.assertIn('--dry-run', command)
                    barrier.wait(timeout=5)
                    raise subprocess.TimeoutExpired(command, kwargs['timeout'])
                preflight = ReleasePreflight(manifest, command_runner=run,
                    environment={'CEDAR_HOME': directory})
                findings = getattr(preflight, check)()
                self.assertEqual(8, len(findings))
                for repo, finding in zip(repos, findings):
                    self.assertIn(repo, finding.message)
                    self.assertIn('timed out after 60s', finding.message)
                    self.assertTrue(finding.fatal)

    def test_timings_include_failed_checks_without_reordering(self):
        preflight = ReleasePreflight({}, environment={})
        with patch.object(preflight, 'check_profile', return_value=[]) as first, \
             patch.object(preflight, 'check_toolchain', side_effect=RuntimeError('failure')):
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                preflight._run_checks(['check_profile', 'check_toolchain'])
        first.assert_called_once()
        self.assertEqual(['check_profile', 'check_toolchain'], list(preflight.check_timings))

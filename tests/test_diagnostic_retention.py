from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from org.metadatacenter.build_diagnostics import retain_failure, prune_failures

class DiagnosticRetentionTest(unittest.TestCase):
    def test_preview_then_apply_preserves_recent_unknown_and_symlinked_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); work = home/'work'; work.mkdir()
            old = retain_failure(work, home); recent = retain_failure(work, home)
            manifest = old/'manifest.json'; record = json.loads(manifest.read_text())
            record['createdAt'] = (datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
            manifest.write_text(json.dumps(record))
            root = old.parent; (root/'unrecognized').mkdir()
            (root/'20000101T000000Z-12345678').mkdir()  # interrupted capture, no manifest
            (root/'20000101T000000Z-87654321').symlink_to(work, target_is_directory=True)
            rows = prune_failures(home)
            self.assertEqual(1, sum(row['action']=='would remove' for row in rows))
            self.assertTrue(old.exists())
            prune_failures(home, apply=True)
            self.assertFalse(old.exists()); self.assertTrue(recent.exists())
            self.assertTrue(work.exists()); self.assertTrue((root/'unrecognized').exists())
            self.assertTrue((root/'20000101T000000Z-12345678').exists())

    def test_budget_and_symlink_root(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); work = home/'work'; work.mkdir()
            bundle = retain_failure(work, home)
            rows = prune_failures(home, max_bytes=0)
            self.assertEqual('would remove', rows[0]['action'])
            self.assertEqual('budget', rows[0]['reason'])
            root = bundle.parent; moved = root.with_name('saved'); root.rename(moved)
            root.symlink_to(moved, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlinked'):
                prune_failures(home, apply=True)
            self.assertTrue(moved.exists())


class NpmDebugLogRetentionTest(unittest.TestCase):
    """npm writes its debug logs into its cache, beside the workspace rather than inside it."""

    def test_npm_logs_are_kept_first_and_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); work = home/'work'; logs = home/'npm-cache'/'_logs'
            (work/'coverage').mkdir(parents=True); logs.mkdir(parents=True)
            (work/'coverage'/'summary.json').write_text('x' * 64)
            (logs/'2026-10-05T02_48_57_158Z-debug-0.log').write_text('21 verbose reify failed optional dependency\n')
            (logs/'linked.log').symlink_to(work/'coverage'/'summary.json')
            bundle = retain_failure(work, home, npm_logs=logs, limit=60)
            files = json.loads((bundle/'manifest.json').read_text())
            self.assertEqual(['npm-logs/2026-10-05T02_48_57_158Z-debug-0.log'], files['files'])
            self.assertEqual(['coverage/summary.json'], files['omittedForSize'])
            self.assertIn('failed optional dependency',
                          (bundle/'npm-logs'/'2026-10-05T02_48_57_158Z-debug-0.log').read_text())

    def test_an_absent_log_directory_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); work = home/'work'; work.mkdir()
            for logs in (None, home/'missing'):
                bundle = retain_failure(work, home, npm_logs=logs)
                self.assertEqual([], json.loads((bundle/'manifest.json').read_text())['files'])

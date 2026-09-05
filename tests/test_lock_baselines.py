import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console
from typer.testing import CliRunner

from org.metadatacenter import publish
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.LockBaselineWorker import LockBaselineWorker


def digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class LockBaselineTest(unittest.TestCase):
    """The mechanical half of re-reviewing a lock the train refuses, for every stale lock at once."""

    def setUp(self):
        self.runner = CliRunner()

    @staticmethod
    def _estate(directory, locks, baselines):
        """A CEDAR_HOME with the given lock contents and the baselines the train is bound to."""
        ops = Path(directory) / "cedar-development" / "ops"
        ops.mkdir(parents=True)
        for (repository, relative), content in locks.items():
            path = Path(directory) / repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        config = {"registry": "https://nexus.example/npm/", "auditBaselines": baselines}
        (ops / "frontend-train.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        return ops / "frontend-train.json"

    def _baseline(self, repository, lock, content, **counts):
        values = {"low": 0, "moderate": 0, "high": 0, "critical": 0}
        values.update(counts)
        return {"repository": repository, "lock": lock, "strictInstallScripts": True,
                "sha256": digest(content), "vulnerabilities": values}

    def test_survey_tells_a_moved_lock_from_a_current_one_and_an_absent_one(self):
        with tempfile.TemporaryDirectory() as directory:
            self._estate(directory, {
                ("cedar-a", "package-lock.json"): '{"a": 2}\n',
                ("cedar-b", "src/package-lock.json"): '{"b": 1}\n',
            }, [
                self._baseline("cedar-a", "package-lock.json", '{"a": 1}\n'),
                self._baseline("cedar-b", "src/package-lock.json", '{"b": 1}\n'),
                self._baseline("cedar-c", "package-lock.json", '{"c": 1}\n'),
            ])
            with patch.object(Util, "cedar_home", directory):
                states = {b.identity: b.state for b in LockBaselineWorker.survey()}

        self.assertEqual({
            "cedar-a:package-lock.json": "stale",
            "cedar-b:src/package-lock.json": "current",
            "cedar-c:package-lock.json": "missing",
        }, states)

    def test_refresh_rewrites_only_the_stale_baselines_and_keeps_the_file_shape(self):
        audited = []

        def auditor(directory):
            audited.append(Path(directory).name)
            return {"low": 1, "moderate": 2, "high": 0, "critical": 0}

        with tempfile.TemporaryDirectory() as directory:
            config = self._estate(directory, {
                ("cedar-a", "package-lock.json"): '{"a": 2}\n',
                ("cedar-b", "src/package-lock.json"): '{"b": 1}\n',
            }, [
                self._baseline("cedar-a", "package-lock.json", '{"a": 1}\n', high=7),
                self._baseline("cedar-b", "src/package-lock.json", '{"b": 1}\n', low=3),
            ])
            buffer = io.StringIO()
            with patch.object(Util, "cedar_home", directory), patch(
                    "org.metadatacenter.worker.LockBaselineWorker.console",
                    Console(file=buffer, width=200, force_terminal=False)):
                code = LockBaselineWorker.refresh(auditor=auditor)
            raw = config.read_text(encoding="utf-8")
            written = json.loads(raw)

        self.assertEqual(0, code, buffer.getvalue())
        self.assertEqual(["cedar-a"], audited)
        refreshed, untouched = written["auditBaselines"]
        self.assertEqual(digest('{"a": 2}\n'), refreshed["sha256"])
        self.assertEqual({"low": 1, "moderate": 2, "high": 0, "critical": 0},
                         refreshed["vulnerabilities"])
        self.assertTrue(refreshed["strictInstallScripts"])
        self.assertEqual(digest('{"b": 1}\n'), untouched["sha256"])
        self.assertEqual(3, untouched["vulnerabilities"]["low"])
        self.assertEqual("https://nexus.example/npm/", written["registry"])
        self.assertTrue(raw.startswith('{\n  "registry"'))
        self.assertTrue(raw.endswith("}\n"))
        self.assertIn("cedar-a:package-lock.json: digest", buffer.getvalue())
        self.assertIn("counts 0/0/7/0 -> 1/2/0/0", buffer.getvalue())
        self.assertIn("commit it in cedar-development", buffer.getvalue())

    def test_refresh_can_be_limited_to_one_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self._estate(directory, {
                ("cedar-a", "package-lock.json"): '{"a": 2}\n',
                ("cedar-b", "package-lock.json"): '{"b": 2}\n',
            }, [
                self._baseline("cedar-a", "package-lock.json", '{"a": 1}\n'),
                self._baseline("cedar-b", "package-lock.json", '{"b": 1}\n'),
            ])
            with patch.object(Util, "cedar_home", directory), patch(
                    "org.metadatacenter.worker.LockBaselineWorker.console",
                    Console(file=io.StringIO(), width=200)):
                code = LockBaselineWorker.refresh(
                    repositories=["cedar-b"], auditor=lambda _d: {s: 0 for s in (
                        "low", "moderate", "high", "critical")})
            written = json.loads(config.read_text(encoding="utf-8"))

        self.assertEqual(0, code)
        self.assertEqual(digest('{"a": 1}\n'), written["auditBaselines"][0]["sha256"])
        self.assertEqual(digest('{"b": 2}\n'), written["auditBaselines"][1]["sha256"])

    def test_nothing_stale_means_nothing_written(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self._estate(directory, {
                ("cedar-a", "package-lock.json"): '{"a": 1}\n',
            }, [self._baseline("cedar-a", "package-lock.json", '{"a": 1}\n')])
            before = config.stat().st_mtime_ns
            buffer = io.StringIO()
            with patch.object(Util, "cedar_home", directory), patch(
                    "org.metadatacenter.worker.LockBaselineWorker.console",
                    Console(file=buffer, width=200)):
                code = LockBaselineWorker.refresh(auditor=lambda _d: self.fail("nothing to audit"))
            self.assertEqual(before, config.stat().st_mtime_ns)

        self.assertEqual(0, code)
        self.assertIn("nothing to refresh", buffer.getvalue())

    def test_audit_counts_come_from_the_json_whatever_the_exit_status(self):
        payload = {"metadata": {"vulnerabilities": {
            "info": 0, "low": 4, "moderate": 1, "high": 0, "critical": 2, "total": 7,
        }}}

        class Result:
            returncode = 1
            stdout = json.dumps(payload)
            stderr = ""

        counts = LockBaselineWorker.audit_counts(
            "/tmp/anywhere", runner=lambda *_args, **_kwargs: Result())

        self.assertEqual({"low": 4, "moderate": 1, "high": 0, "critical": 2}, counts)

    def test_audit_without_counts_is_an_error_that_names_the_directory(self):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "npm error ENOLOCK\nnpm error This command requires an existing lockfile."

        with self.assertRaisesRegex(ValueError, "/tmp/anywhere.*existing lockfile"):
            LockBaselineWorker.audit_counts(
                "/tmp/anywhere", runner=lambda *_args, **_kwargs: Result())

    def test_the_report_lists_stale_locks_and_refuses_for_a_train(self):
        with tempfile.TemporaryDirectory() as directory:
            self._estate(directory, {
                ("cedar-a", "package-lock.json"): '{"a": 2}\n',
                ("cedar-b", "package-lock.json"): '{"b": 1}\n',
            }, [
                self._baseline("cedar-a", "package-lock.json", '{"a": 1}\n'),
                self._baseline("cedar-b", "package-lock.json", '{"b": 1}\n'),
            ])
            buffer = io.StringIO()
            with patch.object(Util, "cedar_home", directory), patch(
                    "org.metadatacenter.worker.LockBaselineWorker.console",
                    Console(file=buffer, width=200, force_terminal=False)):
                result = self.runner.invoke(publish.app, ["baselines"])
        rendered = buffer.getvalue()

        self.assertEqual(1, result.exit_code, rendered)
        self.assertIn("cedar-a:package-lock.json", rendered)
        self.assertNotIn("cedar-b:package-lock.json", rendered)
        self.assertIn("1 current, 1 stale or missing of 2 baselines", rendered)
        self.assertIn("publish baselines --refresh", rendered)

    def test_the_command_refreshes_through_the_worker(self):
        with patch.object(LockBaselineWorker, "refresh", return_value=0) as refresh:
            result = self.runner.invoke(
                publish.app, ["baselines", "--refresh", "--repository", "cedar-a"])
        self.assertEqual(0, result.exit_code, result.output)
        refresh.assert_called_once_with(repositories=["cedar-a"])


if __name__ == "__main__":
    unittest.main()

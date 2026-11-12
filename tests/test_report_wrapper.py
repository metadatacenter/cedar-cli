import subprocess
import tempfile
import unittest
from pathlib import Path

from org.metadatacenter.train_support import report_wrapper


class ReportWrapperRehearsalTest(unittest.TestCase):
    """The dry run lists each wrapped lane both ways, as train 0646's CEE shards needed."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        for repository, lane in report_wrapper.LANES:
            directory = self.home / repository / lane
            (directory / "node_modules").mkdir(parents=True)
            (directory / "playwright.config.ts").write_text("export default {};\n")

    def runner(self, own=1656, wrapped=1656, write_code=0):
        calls = []

        def run(command, cwd=None, **_kwargs):
            calls.append((command, cwd))
            if command[1] == "-c":
                Path(command[4], report_wrapper.WRAPPER).write_text("wrapper\n")
                return subprocess.CompletedProcess(command, write_code, "", "no module")
            count = wrapped if any(part.startswith("--config=") for part in command) else own
            listing = "" if count is None else f"Total: {count} tests in 38 files\n"
            return subprocess.CompletedProcess(command, 0 if count is not None else 1, listing, "")
        return run, calls

    def test_matching_lists_raise_nothing_and_leave_no_file_behind(self):
        run, calls = self.runner()
        self.assertEqual([], report_wrapper.wrapper_findings(self.home, run=run))
        self.assertEqual(6, len(calls))
        for repository, lane in report_wrapper.LANES:
            self.assertFalse((self.home / repository / lane / report_wrapper.WRAPPER).exists())

    def test_a_wrapper_that_loads_nothing_is_a_finding(self):
        run, _ = self.runner(wrapped=None)
        findings = report_wrapper.wrapper_findings(self.home, run=run)
        self.assertEqual(2, len(findings))
        self.assertIn("lists no tests in cedar-embeddable-editor visual, where its own config "
                      "lists 1656", findings[0])

    def test_a_lane_without_an_install_is_not_rehearsed(self):
        for repository, lane in report_wrapper.LANES:
            (self.home / repository / lane / "node_modules").rmdir()
        run, calls = self.runner(wrapped=None)
        self.assertEqual([], report_wrapper.wrapper_findings(self.home, run=run))
        self.assertEqual([], calls)

    def test_a_wrapper_already_in_the_lane_is_left_alone(self):
        existing = self.home / "cedar-embeddable-editor" / "visual" / report_wrapper.WRAPPER
        existing.write_text("someone's own\n")
        run, _ = self.runner()
        findings = report_wrapper.wrapper_findings(self.home, run=run)
        self.assertEqual("someone's own\n", existing.read_text())
        self.assertIn("already exists", findings[0])

    def test_the_train_s_own_writer_failing_is_a_finding(self):
        run, _ = self.runner(write_code=1)
        findings = report_wrapper.wrapper_findings(self.home, run=run)
        self.assertIn("cannot write its Playwright report wrapper", findings[0])

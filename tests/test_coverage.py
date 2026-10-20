from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import tempfile
import time
import unittest

from rich.console import Console

from org.metadatacenter import coverage
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context

REPORT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="Example"><sessioninfo id="s" start="1" dump="2"/>
<package name="org/metadatacenter/example">
<class name="org/metadatacenter/example/Folder" sourcefilename="Folder.java">
<counter type="LINE" missed="99" covered="99"/></class>
<class name="org/metadatacenter/example/Folder$Child" sourcefilename="Folder.java"/>
<counter type="LINE" missed="98" covered="98"/></package>
<counter type="INSTRUCTION" missed="40" covered="60"/>
<counter type="BRANCH" missed="3" covered="1"/>
<counter type="LINE" missed="6" covered="4"/>
</report>
"""


def write(path: Path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class CoverageTest(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()

    def module_report(self, module, name="jacoco", text=REPORT):
        site = self.home / module / "target" / "site" / name
        write(site / "jacoco.xml", text)
        return write(site / "index.html")

    def test_reports_and_execution_data_are_those_written_since_the_build_started(self):
        reports = {name: self.module_report(path, kind) for name, path, kind in (
            ("unit", "cedar-artifact-server/application", "jacoco"),
            ("integration", "cedar-artifact-server/application", "jacoco-it"),
            ("nested", "cedar-libraries-member/a/b", "jacoco"),
            ("stale", "cedar-user-server/application", "jacoco"),
            ("dependency", "cedar-artifact-server/node_modules/x", "jacoco"),
        )}
        executions = {name: write(self.home / path, text) for name, path, text in (
            ("written", "cedar-artifact-server/application/target/jacoco.exec", "data"),
            ("empty", "cedar-libraries-member/a/b/target/jacoco.exec", ""),
            ("stale", "cedar-user-server/application/target/jacoco.exec", "data"),
        )}
        started = time.time() - 10
        for stale in (reports["stale"], executions["stale"]):
            os.utime(stale, (started - 60, started - 60))
        checkouts = {self.home / name for name in (
            "cedar-artifact-server", "cedar-libraries-member", "cedar-user-server")}

        self.assertEqual({reports["unit"], reports["integration"], reports["nested"]},
                         coverage.coverage_reports(checkouts, started))
        self.assertEqual([executions["written"]], coverage.execution_data(checkouts, started))

    def test_a_report_gives_its_class_files_and_its_own_root_counters(self):
        report = self.module_report("cedar-x/app").parent / "jacoco.xml"

        classes, lines, branches = coverage.read_report(report)

        target = self.home / "cedar-x" / "app" / "target" / "classes" / "org" / "metadatacenter" / "example"
        self.assertEqual([target / "Folder.class", target / "Folder$Child.class"], classes)
        self.assertEqual((4, 10), lines)
        self.assertEqual((1, 4), branches)

    def test_the_classpath_is_the_jacoco_release_cedar_parent_pins_and_its_asm(self):
        write(self.home / "cedar-parent" / "pom.xml",
              '<project xmlns="http://maven.apache.org/POM/4.0.0"><properties>'
              '<jacoco.maven.plugin.version>0.8.15</jacoco.maven.plugin.version></properties></project>')
        repository = self.home / "m2"
        write(repository / "org/jacoco/org.jacoco.build/0.8.15/org.jacoco.build-0.8.15.pom",
              '<project xmlns="http://maven.apache.org/POM/4.0.0"><properties>'
              '<asm.version>9.10.1</asm.version></properties></project>')
        jars = [repository / "org/jacoco/org.jacoco.core/0.8.15/org.jacoco.core-0.8.15.jar"]
        jars += [repository / f"org/ow2/asm/{name}/9.10.1/{name}-9.10.1.jar"
                 for name in ("asm", "asm-commons", "asm-tree")]
        for jar in jars[:-1]:
            write(jar)

        with self.assertRaisesRegex(coverage.CoverageUnavailable, "asm-tree-9.10.1.jar"):
            coverage.jacoco_classpath(self.home, repository)
        write(jars[-1])
        self.assertEqual(jars, coverage.jacoco_classpath(self.home, repository))

    def test_the_helper_rows_become_figures_and_a_failure_says_why(self):
        module = self.home / "cedar-x" / "app"
        ran = SimpleNamespace(returncode=0, stdout=f"{module}\t7\t10\t2\t4\n", stderr="")
        with patch.object(coverage.subprocess, "run", return_value=ran):
            self.assertEqual({module: (7, 10, 2, 4)},
                             coverage.merged_figures({module: []}, [], Path("java"), []))

        failed = SimpleNamespace(returncode=1, stdout="", stderr="trace\nInvalid execution data file\n")
        with patch.object(coverage.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(coverage.CoverageUnavailable, "exited 1: Invalid execution data"):
                coverage.merged_figures({module: []}, [], Path("java"), [])

    def test_each_module_is_credited_by_its_own_report_and_by_any_test(self):
        unit = self.module_report("cedar-x/app")
        integration = self.module_report("cedar-x/app", "jacoco-it",
                                         REPORT.replace('type="LINE" missed="6" covered="4"',
                                                        'type="LINE" missed="0" covered="10"'))
        other = self.module_report("cedar-y")
        any_test = {self.home / "cedar-x" / "app": (9, 10, 3, 4), self.home / "cedar-y": (4, 10, 1, 4)}
        with patch.object(coverage, "merged_figures", return_value=any_test), \
                patch.object(coverage, "jacoco_classpath", return_value=[]):
            measured = coverage.measure({integration, unit, other}, [], self.home, self.home, Path("java"))
            summary = coverage.summarize(measured, self.home)

        # The Surefire report supplies the own-test figure when Failsafe wrote a second one.
        self.assertEqual({"lines": (4, 10, 9), "branches": (1, 4, 3)},
                         measured[self.home / "cedar-x" / "app"])
        self.assertEqual({"total": 20, "ownCovered": 8, "anyCovered": 13,
                          "ownPercent": 40.0, "anyPercent": 65.0}, summary["lines"])
        self.assertEqual(["cedar-x", "cedar-y"], [entry["repository"] for entry in summary["repositories"]])
        self.assertEqual(["cedar-x/app", "cedar-y"], [entry["module"] for entry in summary["modules"]])

    def test_a_module_whose_classes_changed_after_its_report_is_not_misreported(self):
        report = self.module_report("cedar-x/app")
        with patch.object(coverage, "merged_figures", return_value={self.home / "cedar-x/app": (9, 12, 3, 4)}), \
                patch.object(coverage, "jacoco_classpath", return_value=[]):
            with self.assertRaisesRegex(coverage.CoverageUnavailable, "changed after its report"):
                coverage.measure({report}, [], self.home, self.home, Path("java"))

    def run_report(self, measured=None, error=None):
        output = StringIO()
        reports = {self.home / "cedar-x/target/site/jacoco/index.html"}
        measure = patch.object(coverage, "measure", return_value=measured, side_effect=error)
        with use_context(InvocationContext(environment={"CEDAR_HOME": str(self.home), "HOME": str(self.home)})), \
                patch.object(coverage, "java_checkouts", return_value=set()), \
                patch.object(coverage, "coverage_reports", return_value=reports), \
                patch.object(coverage, "execution_data", return_value=[]), \
                patch.object(coverage.ModeManager, "java_17_home", return_value="/jdk"), \
                patch.object(coverage, "console", Console(file=output, width=40)), measure:
            coverage.report_coverage(0)
        return output.getvalue().splitlines()

    def test_a_build_reports_both_figures_in_one_unwrapped_line_and_a_file(self):
        lines = self.run_report(measured={self.home / "cedar-x": {"lines": (534, 1000, 687),
                                                                   "branches": (1, 4, 3)}})

        written = sorted((self.home / ".cedar" / "coverage").glob("*.json"))
        self.assertEqual(1, len(written))
        self.assertEqual([
            "JaCoCo wrote 1 coverage report under the tested modules' target/site directories.",
            f"Line coverage: 68.7% by any test, 53.4% by each module's own tests (1,000 lines in 1 "
            f"module). Per module: {written[0]}",
        ], lines)
        self.assertEqual(68.7, json.loads(written[0].read_text())["repositories"][0]["lines"]["anyPercent"])

    def test_a_build_that_cannot_measure_says_why_and_writes_nothing(self):
        lines = self.run_report(error=coverage.CoverageUnavailable("the local repository lacks asm-9.10.1.jar"))

        self.assertEqual("No merged coverage figure, because the local repository lacks asm-9.10.1.jar.",
                         lines[-1])
        self.assertFalse((self.home / ".cedar" / "coverage").exists())


if __name__ == "__main__":
    unittest.main()

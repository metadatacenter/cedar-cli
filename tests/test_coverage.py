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
        execution = self.home / "cedar-x" / "app" / "target" / "jacoco.exec"
        written = {}

        def run(command, **_):
            written.update({Path(argument).name: Path(argument).read_text() for argument in command[-2:]})
            return SimpleNamespace(returncode=0, stdout=f"{module}\t7\t10\t2\t4\t5\t1\n", stderr="")
        with patch.object(coverage.subprocess, "run", side_effect=run):
            self.assertEqual({module: (7, 10, 2, 4, 5, 1)},
                             coverage.merged_figures({module: ("cedar-x", [])}, [("cedar-x", execution)],
                                                     Path("java"), []))
        # Each execution-data file and each module goes to the helper under its repository.
        self.assertEqual(f"cedar-x\t{execution}\n", written["executions.txt"])
        self.assertEqual(f"cedar-x\t{module}\n", written["modules.txt"])

        failed = SimpleNamespace(returncode=1, stdout="", stderr="trace\nInvalid execution data file\n")
        with patch.object(coverage.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(coverage.CoverageUnavailable, "exited 1: Invalid execution data"):
                coverage.merged_figures({module: ("cedar-x", [])}, [], Path("java"), [])

    def test_each_module_is_credited_by_its_own_report_by_its_repository_and_by_any_test(self):
        unit = self.module_report("cedar-x/app")
        integration = self.module_report("cedar-x/app", "jacoco-it",
                                         REPORT.replace('type="LINE" missed="6" covered="4"',
                                                        'type="LINE" missed="0" covered="10"'))
        other = self.module_report("cedar-y")
        executions = [self.home / "cedar-x/app/target/jacoco.exec", self.home / "cedar-y/target/jacoco.exec"]
        any_test = {self.home / "cedar-x" / "app": (9, 10, 3, 4, 7, 2), self.home / "cedar-y": (4, 10, 1, 4, 4, 1)}
        with patch.object(coverage, "merged_figures", return_value=any_test) as merged, \
                patch.object(coverage, "jacoco_classpath", return_value=[]):
            measured = coverage.measure({integration, unit, other}, executions, self.home, self.home, Path("java"))
            summary = coverage.summarize(measured, self.home)

        modules, grouped = merged.call_args.args[:2]
        self.assertEqual({self.home / "cedar-x" / "app": "cedar-x", self.home / "cedar-y": "cedar-y"},
                         {module: repository for module, (repository, _) in modules.items()})
        self.assertEqual([("cedar-x", executions[0]), ("cedar-y", executions[1])], grouped)
        # The Surefire report supplies the own-test figure when Failsafe wrote a second one.
        self.assertEqual({"lines": (4, 10, 9, 7), "branches": (1, 4, 3, 2)},
                         measured[self.home / "cedar-x" / "app"])
        self.assertEqual({"total": 20, "ownCovered": 8, "repositoryCovered": 11, "anyCovered": 13,
                          "ownPercent": 40.0, "repositoryPercent": 55.0, "anyPercent": 65.0}, summary["lines"])
        self.assertEqual(["cedar-x", "cedar-y"], [entry["repository"] for entry in summary["repositories"]])
        self.assertEqual(["cedar-x/app", "cedar-y"], [entry["module"] for entry in summary["modules"]])

    def test_a_module_whose_classes_changed_after_its_report_is_not_misreported(self):
        report = self.module_report("cedar-x/app")
        with patch.object(coverage, "merged_figures", return_value={self.home / "cedar-x/app": (9, 12, 3, 4, 9, 3)}), \
                patch.object(coverage, "jacoco_classpath", return_value=[]):
            with self.assertRaisesRegex(coverage.CoverageUnavailable, "changed after its report"):
                coverage.measure({report}, [], self.home, self.home, Path("java"))

    def run_report(self, measured=None, error=None, gate=False):
        output = StringIO()
        reports = {self.home / "cedar-x/target/site/jacoco/index.html"}
        measure = patch.object(coverage, "measure", return_value=measured, side_effect=error)
        with use_context(InvocationContext(environment={"CEDAR_HOME": str(self.home), "HOME": str(self.home)})), \
                patch.object(coverage, "java_checkouts", return_value=set()), \
                patch.object(coverage, "coverage_reports", return_value=reports), \
                patch.object(coverage, "execution_data", return_value=[]), \
                patch.object(coverage.ModeManager, "java_17_home", return_value="/jdk"), \
                patch.object(coverage, "console", Console(file=output, width=40)), measure:
            coverage.report_coverage(0, gate=gate)
        return output.getvalue().splitlines()

    def test_a_build_reports_the_three_figures_in_one_unwrapped_line_and_a_file(self):
        lines = self.run_report(measured={self.home / "cedar-x": {"lines": (534, 1000, 687, 601),
                                                                   "branches": (1, 4, 3, 2)}})

        written = sorted((self.home / ".cedar" / "coverage").glob("*.json"))
        self.assertEqual(1, len(written))
        self.assertEqual([
            "JaCoCo wrote 1 coverage report under the tested modules' target/site directories.",
            f"Line coverage: 68.7% by any test, 60.1% by each repository's own tests, 53.4% by each "
            f"module's own tests (1,000 lines in 1 module). Per module: {written[0]}",
        ], lines)
        self.assertEqual(68.7, json.loads(written[0].read_text())["repositories"][0]["lines"]["anyPercent"])

    def test_a_build_that_cannot_measure_says_why_and_writes_nothing(self):
        lines = self.run_report(error=coverage.CoverageUnavailable("the local repository lacks asm-9.10.1.jar"))

        self.assertEqual("No merged coverage figure, because the local repository lacks asm-9.10.1.jar.",
                         lines[-1])
        self.assertFalse((self.home / ".cedar" / "coverage").exists())


    def test_the_ci_entry_measures_every_checkout_in_its_workspace_with_the_given_java(self):
        for name, pom in (("cedar-project", True), ("cedar-artifact-server", True), ("cedar-cli", False)):
            write(self.home / name / ("pom.xml" if pom else "README.md"))
        with patch.object(coverage, "report") as report:
            coverage.main([str(self.home), "--java", "/jdk/bin/java", "--repository", "/m2"])

        checkouts, started, home, repository, java = report.call_args.args
        self.assertEqual({self.home / "cedar-project", self.home / "cedar-artifact-server"}, checkouts)
        self.assertEqual((0, self.home, Path("/m2"), Path("/jdk/bin/java")), (started, home, repository, java()))

    @staticmethod
    def summary(*modules):
        """A summary with each (module, repository percent, any percent) as its line and branch figures."""
        return {'modules': [
            {'module': name, **{kind: {'repositoryPercent': repository, 'anyPercent': any_test}
                                for kind in ('lines', 'branches')}}
            for name, repository, any_test in modules]}

    def test_the_gate_names_a_figure_more_than_two_points_below_its_recorded_value(self):
        recorded = {'cedar-x/core': {'lines': {'repository': 97.6, 'any': 97.6},
                                     'branches': {'repository': 85.9, 'any': 85.9}}}

        self.assertEqual([], coverage.regressions(self.summary(('cedar-x/core', 95.6, 97.6)), recorded))
        self.assertEqual([], coverage.regressions(
            {'modules': [{'module': 'cedar-x/core',
                          'lines': {'repositoryPercent': 99.0, 'anyPercent': 99.0},
                          'branches': {'repositoryPercent': 83.9, 'anyPercent': 83.9}}]}, recorded))
        # The bridge core's own tests cover 4.5% of it; its repository's tests cover 97.6%.
        self.assertEqual([
            "cedar-x/core: branches covered by its repository's tests fell to 4.5% from 85.9% recorded",
            "cedar-x/core: lines covered by its repository's tests fell to 4.5% from 97.6% recorded",
        ], coverage.regressions(self.summary(('cedar-x/core', 4.5, 97.6)), recorded))
        self.assertEqual([
            "cedar-x/core: branches covered by any test fell to 83.8% from 85.9% recorded",
            "cedar-x/core: lines covered by any test fell to 95.5% from 97.6% recorded",
        ], coverage.regressions(
            {'modules': [{'module': 'cedar-x/core',
                          'lines': {'repositoryPercent': 97.6, 'anyPercent': 95.5},
                          'branches': {'repositoryPercent': 85.9, 'anyPercent': 83.8}}]}, recorded))

    def test_the_gate_fails_a_recorded_module_that_was_not_measured_and_ignores_a_new_one(self):
        recorded = {'cedar-x/core': {'lines': {'repository': 50.0, 'any': 60.0}}}

        self.assertEqual(["cedar-x/core has recorded figures but none was measured"],
                         coverage.regressions(self.summary(('cedar-y/new', 0.0, 0.0)), recorded))
        self.assertEqual(["cedar-x/core: lines covered by its repository's tests fell to None% from 50.0% recorded",
                          "cedar-x/core: lines covered by any test fell to None% from 60.0% recorded"],
                         coverage.regressions(self.summary(('cedar-x/core', None, None)), recorded))

    def test_the_committed_recorded_figures_name_modules_under_the_estate(self):
        recorded = coverage.read_recorded()

        # The modules whose own tests cover little of what their repository's tests reach.
        for module in ('cedar-bridge-server/cedar-bridge-server-core',
                       'cedar-microservice-libraries/cedar-workspace-operations-library',
                       'cedar-microservice-libraries/cedar-template-operations-library'):
            self.assertIn(module, recorded)
        for module, kinds in recorded.items():
            self.assertRegex(module, r'^cedar-[\w-]+(/[\w-]+)?$')
            self.assertEqual({'lines', 'branches'}, set(kinds))
            for scopes in kinds.values():
                self.assertEqual({'repository', 'any'}, set(scopes))

    def test_recorded_figures_round_trip_one_line_per_module(self):
        summary = self.summary(('cedar-y/b', 40.0, 85.7), ('cedar-x/a', 97.6, 97.6))
        path = self.home / 'recorded.json'

        coverage.write_recorded(coverage.recorded_figures(summary, '20261115T205125Z.json'), path)

        self.assertEqual({'cedar-x/a': {'lines': {'repository': 97.6, 'any': 97.6},
                                        'branches': {'repository': 97.6, 'any': 97.6}},
                          'cedar-y/b': {'lines': {'repository': 40.0, 'any': 85.7},
                                        'branches': {'repository': 40.0, 'any': 85.7}}},
                         coverage.read_recorded(path))
        self.assertEqual(8, len(path.read_text().splitlines()))

    def test_a_gated_build_fails_when_a_figure_falls_or_cannot_be_computed(self):
        recorded = self.home / 'recorded.json'
        coverage.write_recorded({'schemaVersion': 1, 'source': 'x', 'modules': {
            'cedar-x': {'lines': {'repository': 70.0, 'any': 70.0}}}}, recorded)
        measured = {self.home / "cedar-x": {"lines": (534, 1000, 687, 601), "branches": (1, 4, 3, 2)}}
        with patch.object(coverage, 'RECORDED', recorded):
            self.run_report(measured=measured)
            with self.assertRaises(coverage.CoverageRegression) as raised:
                self.run_report(measured=measured, gate=True)
            self.assertEqual(["cedar-x: lines covered by its repository's tests fell to 60.1% from 70.0% recorded"],
                             raised.exception.reasons)
            with self.assertRaisesRegex(coverage.CoverageRegression, "because the local repository lacks"):
                self.run_report(error=coverage.CoverageUnavailable("the local repository lacks asm"), gate=True)
            coverage.write_recorded({'schemaVersion': 1, 'source': 'x', 'modules': {
                'cedar-x': {'lines': {'repository': 62.0, 'any': 70.0}}}}, recorded)
            self.run_report(measured=measured, gate=True)

    def coverage_records(self, now):
        """Two records the module wrote, one 20 days old, beside entries it must leave alone."""
        from datetime import timedelta
        root = self.home / ".cedar" / "coverage"
        def record(age):
            measured = (now - timedelta(days=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
            name = measured.replace("-", "").replace(":", "") + ".json"
            return write(root / name, json.dumps({"schemaVersion": 1, "measuredAt": measured}))
        old, recent = record(20), record(1)
        write(root / "notes.json", old.read_text())
        write(root / "20261002T120000Z.json", "not json")
        (root / "20261003T120000Z.json").symlink_to(old)
        return old, recent

    def test_coverage_records_are_previewed_then_removed_by_age_alone(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        old, recent = self.coverage_records(now)

        preview = coverage.prune_coverage(self.home, days=14, now=now)
        self.assertEqual([(str(recent), "keep"), (str(old), "would remove")],
                         [(row["path"], row["action"]) for row in preview])
        self.assertTrue(old.exists())

        applied = coverage.prune_coverage(self.home, days=14, apply=True, now=now)
        self.assertEqual(["keep", "removed"], [row["action"] for row in applied])
        self.assertFalse(old.exists())
        self.assertEqual({recent.name, "notes.json", "20261002T120000Z.json", "20261003T120000Z.json"},
                         {path.name for path in (self.home / ".cedar" / "coverage").iterdir()})

    def test_only_the_builds_that_span_every_java_reactor_gate_coverage(self):
        from typer.testing import CliRunner
        from org.metadatacenter import build
        runner = CliRunner()
        for command, gated in ((["java"], True), (["all", "--skip-tests"], True), (["project"], False),
                               (["libraries"], False)):
            with self.subTest(command=command), \
                    patch.object(build, "require_integration_tags"), \
                    patch.object(build, "require_design_token_adoption"), \
                    patch.object(build, "execute_build", return_value=None), \
                    patch.object(build, "report_coverage") as reported:
                result = runner.invoke(build.app, command)
                self.assertEqual(0, result.exit_code, result.output)
                if "--skip-tests" in command:
                    reported.assert_not_called()
                else:
                    reported.assert_called_once()
                    self.assertEqual(gated, reported.call_args.kwargs["gate"])

    def test_a_coverage_regression_fails_build_java_and_says_how_to_record_new_figures(self):
        from typer.testing import CliRunner
        from org.metadatacenter import build
        regression = coverage.CoverageRegression(["cedar-x/core: lines covered by any test fell to 4.5% from 97.6% recorded"])
        with patch.object(build, "require_integration_tags"), \
                patch.object(build, "execute_build", return_value=None), \
                patch.object(build, "report_coverage", side_effect=regression):
            result = CliRunner().invoke(build.app, ["java"])

        self.assertEqual(1, result.exit_code, result.output)
        output = " ".join(result.output.split())
        self.assertIn("cedar-x/core: lines covered by any test fell to 4.5% from 97.6% recorded", output)
        self.assertIn("cedarcli build record-coverage --apply", output)

    def test_record_coverage_previews_then_writes_the_newest_record(self):
        from typer.testing import CliRunner
        from org.metadatacenter import build
        from org.metadatacenter.util.Util import Util
        records = self.home / ".cedar" / "coverage"
        write(records / "20261114T000000Z.json", json.dumps(self.summary(("cedar-x", 10.0, 10.0))))
        write(records / "20261115T205125Z.json", json.dumps(self.summary(("cedar-x", 97.6, 98.0))))
        recorded = self.home / "assets" / "recorded.json"
        with patch.object(Util, "cedar_home", str(self.home)), patch.object(coverage, "RECORDED", recorded):
            preview = CliRunner().invoke(build.app, ["record-coverage"])
            self.assertEqual(0, preview.exit_code, preview.output)
            self.assertFalse(recorded.exists())
            applied = CliRunner().invoke(build.app, ["record-coverage", "--apply"])
            self.assertEqual(0, applied.exit_code, applied.output)
            self.assertEqual({"cedar-x": {"lines": {"repository": 97.6, "any": 98.0},
                                          "branches": {"repository": 97.6, "any": 98.0}}},
                             coverage.read_recorded(recorded))

    def test_build_diagnostics_lists_coverage_records_beside_failure_bundles(self):
        from typer.testing import CliRunner
        from org.metadatacenter import build
        from org.metadatacenter.util.Util import Util
        from datetime import datetime, timezone
        old, recent = self.coverage_records(datetime.now(timezone.utc))
        with patch.object(Util, "cedar_home", str(self.home)):
            result = CliRunner().invoke(build.app, ["diagnostics", "--days", "14"])

        self.assertEqual(0, result.exit_code, result.output)
        # Each row is one line, so a path can be copied from the output whole.
        self.assertIn(f"would remove: {old} (", result.output)
        self.assertIn(f"keep: {recent} (", result.output)
        self.assertIn("2 recognized coverage records", " ".join(result.output.split()))
        self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from CedarCliSettings import CedarCliSettings
from org.metadatacenter import build, clean_maven, publish
from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util


class BuildPolicyTest(unittest.TestCase):

    @staticmethod
    def plain_output(output):
        return re.sub(r"\x1b\[[0-9;]*m", "", output)

    def setUp(self):
        CedarCliSettings.skip_tests = False
        CedarCliSettings.do_fail_on_error = True
        GlobalContext.init_task_operators()
        self.runner = CliRunner()

    def tearDown(self):
        CedarCliSettings.skip_tests = False
        CedarCliSettings.do_fail_on_error = True

    @staticmethod
    def commands(plan):
        result = []
        for task in plan.tasks:
            if task.command_list:
                result.extend(task.command_list)
            result.extend(BuildPolicyTest.commands(task))
        return result

    @patch.object(build.plan_executor, "execute")
    def test_java_build_runs_tests_by_default(self, execute):
        result = self.runner.invoke(build.app, ["java", "--dry-run"])

        self.assertEqual(0, result.exit_code, result.output)
        plan = execute.call_args.args[0]
        maven_commands = [
            command for command in self.commands(plan)
            if command.startswith("./mvnw clean install")
        ]
        self.assertTrue(maven_commands)
        self.assertTrue(all("-DskipTests" not in command for command in maven_commands))

    @patch.object(build.plan_executor, "execute")
    def test_java_build_can_explicitly_skip_tests(self, execute):
        result = self.runner.invoke(
            build.app, ["java", "--skip-tests", "--dry-run"])

        self.assertEqual(0, result.exit_code, result.output)
        plan = execute.call_args.args[0]
        maven_commands = [
            command for command in self.commands(plan)
            if command.startswith("./mvnw clean install")
        ]
        self.assertTrue(maven_commands)
        self.assertTrue(all(command.endswith("-DskipTests")
                            for command in maven_commands))

    def test_test_option_is_only_on_commands_that_can_reach_java(self):
        for command in (
                "this", "parent", "libraries", "project", "clients", "java", "all"):
            result = self.runner.invoke(build.app, [command, "--help"])
            self.assertEqual(0, result.exit_code, result.output)
            output = self.plain_output(result.output)
            self.assertIn("--tests", output)
            self.assertIn("--skip-tests", output)

        for command in ("frontends", "split-frontends"):
            result = self.runner.invoke(build.app, [command, "--help"])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertNotIn("--skip-tests", self.plain_output(result.output))

    @patch.object(clean_maven.CleanMavenWorker, "all")
    @patch.object(clean_maven.CleanMavenWorker, "cedar")
    def test_maven_cache_cleaning_is_nested_under_build(self, clean_cedar, clean_all):
        cedar_result = self.runner.invoke(build.app, ["maven", "clean", "cedar"])
        all_result = self.runner.invoke(build.app, ["maven", "clean", "all"])

        self.assertEqual(0, cedar_result.exit_code, cedar_result.output)
        self.assertEqual(0, all_result.exit_code, all_result.output)
        clean_cedar.assert_called_once_with()
        clean_all.assert_called_once_with()

    def test_maven_is_not_a_top_level_command(self):
        import cedar

        build_help = self.runner.invoke(cedar.app, ["build", "maven", "clean", "--help"])
        top_level = self.runner.invoke(cedar.app, ["maven", "--help"])

        self.assertEqual(0, build_help.exit_code, build_help.output)
        self.assertEqual(2, top_level.exit_code, top_level.output)

    def test_openview_build_runs_its_production_asset_gate(self):
        repos = ReposFactory.build_repos()
        openview = repos.map["cedar-openview"]
        openview_source = next(repo for repo in openview.sub_repos
                               if repo.name == "cedar-openview-src")

        self.assertEqual(
            ['npm ci --legacy-peer-deps', 'npm run build'],
            openview_source.build_command_list)

    @patch.dict("os.environ", {
        "CEDAR_HOME": "/tmp/CEDAR",
        "CEDAR_DEV_BUILD_FRONTENDS": "true",
    })
    @patch.object(build.plan_executor, "execute")
    def test_frontend_build_compiles_without_materializing_distributions(self, execute):
        result = self.runner.invoke(build.app, ["frontends", "--dry-run"])

        self.assertEqual(0, result.exit_code, result.output)
        commands = self.commands(execute.call_args.args[0])
        self.assertIn('npm run build', commands)
        self.assertIn('npm ci --legacy-peer-deps', commands)
        self.assertFalse(any(re.match(
            r"^npm(?:\s+--prefix\s+\S+)?\s+install(?:\s|$)", command)
            for command in commands))
        self.assertFalse(any(command.startswith(("cp -a ", "cat ")) for command in commands))
        for distribution in (
                "cedar-monitoring-dist", "cedar-bridging-dist", "cedar-openview-dist",
                "cedar-cee-demo-angular-dist"):
            self.assertFalse(any(distribution in command for command in commands))
        isolated = [
            task for task in execute.call_args.args[0].tasks
            for task in task.tasks
            for task in task.tasks
            if task.command_list
        ]
        self.assertTrue(isolated)
        self.assertTrue(all(
            task.get_parameter("isolated_frontend_build") is True
            for task in isolated
            if task.repo.repo_type in {"angular", "angularJS", "typescript"}
        ))

    @patch.dict("os.environ", {
        "CEDAR_HOME": "/tmp/CEDAR",
        "CEDAR_DEV_BUILD_FRONTENDS": "true",
    })
    @patch.object(build.plan_executor, "execute")
    def test_full_build_does_not_materialize_frontend_distributions(self, execute):
        result = self.runner.invoke(build.app, ["all", "--dry-run"])

        self.assertEqual(0, result.exit_code, result.output)
        commands = self.commands(execute.call_args.args[0])
        self.assertTrue(any(command.startswith("./mvnw clean install") for command in commands))
        self.assertIn('npm run build', commands)
        self.assertFalse(any(command.startswith(("cp -a ", "cat ")) for command in commands))

    def test_build_fails_when_tracked_estate_state_changes_from_its_baseline(self):
        baseline = {Path("/tmp/cedar-example"): b"pre-existing diff"}
        changed = {Path("/tmp/cedar-example"): b"build-generated diff"}
        with patch.object(build, "capture_estate_state", side_effect=[baseline, changed]), \
                patch.object(build.plan_executor, "execute"), \
                patch.object(Util, "cedar_home", "/tmp"):
            with self.assertRaises(SystemExit) as raised:
                build.execute_build(Plan("guarded"), dry_run=False, dump_plan=False)

        self.assertEqual(1, raised.exception.code)

    @patch.dict("os.environ", {"CEDAR_HOME": "/tmp/CEDAR"})
    @patch.object(Util, "cedar_home", "/tmp/CEDAR")
    @patch.object(publish.plan_executor, "execute")
    def test_explicit_frontend_publish_still_materializes_distributions(self, execute):
        result = self.runner.invoke(publish.app, ["frontends", "--dry-run"])

        self.assertEqual(0, result.exit_code, result.output)
        commands = self.commands(execute.call_args.args[0])
        copy_commands = [command for command in commands if command.startswith("cp -a ")]
        self.assertEqual(4, len(copy_commands))
        for distribution in (
                "cedar-monitoring-dist", "cedar-bridging-dist", "cedar-openview-dist",
                "cedar-cee-demo-angular-dist"):
            self.assertTrue(any(distribution in command for command in copy_commands))

    def test_continue_mode_runs_all_commands_and_returns_the_first_failure(self):
        executor = ShellTaskExecutor()
        task = SimpleNamespace(
            node_id=3,
            repo=SimpleNamespace(name="cedar-example", repo_type="JAVA"),
            command_list=["first", "second"],
        )

        with patch.object(GlobalContext, "fail_on_error", return_value=False), \
                patch("org.metadatacenter.taskexecutor.ShellTaskExecutor.Util.get_wd",
                      return_value="/tmp"), \
                patch.object(
                    executor,
                    "execute_shell_command",
                    side_effect=[([], 7), ([], 0)],
                ) as execute:
            return_code = executor.execute_shell_command_list(
                task, Mock(), dry_run=False)

        self.assertEqual(7, return_code)
        self.assertEqual(2, execute.call_count)

    def test_a_failing_task_halts_the_plan_nonzero(self):
        """
        The default. `fail_on_error` is on unless something turns it off, and
        nothing does, so this is the path every failed build takes: a task
        returns non-zero, the run stops where it stands, and the process says
        so. The continue path below is pinned and this one was not, which is the
        wrong way round — it is the one that runs.
        """
        repo = SimpleNamespace(name="cedar-example", pre_post_type=None)
        task = PlanTask("Maven clean install", TaskType.SHELL, repo)
        task.set_node_id(1)
        executor = PlanExecutor()
        failing = SimpleNamespace(execute=lambda *_: 1)

        with patch.object(GlobalContext, "fail_on_error", return_value=True), \
                patch.object(GlobalContext, "get_task_executor", return_value=failing), \
                patch("org.metadatacenter.executor.PlanExecutor.console") as console:
            with self.assertRaises(SystemExit) as raised:
                executor.execute_recursively(
                    task, 1, 0, [], Mock(), Mock(), Mock(),
                    self.progress_stub(), self.progress_stub(), 0, dry_run=False)

        self.assertEqual(1, raised.exception.code)
        halt_panel = console.print.call_args_list[-1].args[0]
        self.assertEqual("Execution halted", halt_panel.title)

    @staticmethod
    def progress_stub():
        """Enough of a `rich` Progress for the walk to update as it goes."""
        job = SimpleNamespace(description="", completed=0, total=100, finished=False, id=0)
        return SimpleNamespace(tasks=[job], advance=lambda _id: None,
                               update=lambda *_, **__: None, print=lambda *_, **__: None)

    def test_continued_failures_end_the_plan_nonzero(self):
        plan = Plan("Example")
        executor = PlanExecutor()

        with patch(
                "org.metadatacenter.executor.PlanExecutor.Live"), \
                patch.object(
                    executor,
                    "execute_recursively",
                    return_value=["task #1 example exited 7"],
                ), \
                patch(
                    "org.metadatacenter.executor.PlanExecutor.console") as console:
            with self.assertRaises(SystemExit) as raised:
                executor.start_long_execution(plan, dry_run=False)

        self.assertEqual(1, raised.exception.code)
        final_panel = console.print.call_args_list[-1].args[0]
        self.assertEqual("Execution completed with failures", final_panel.title)
        self.assertIn("1 failed task(s)", str(final_panel.renderable))


if __name__ == "__main__":
    unittest.main()

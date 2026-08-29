import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from CedarCliSettings import CedarCliSettings
from org.metadatacenter import build, clean_maven
from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
from org.metadatacenter.util.GlobalContext import GlobalContext


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
            ['npm install --legacy-peer-deps', 'npm run build'],
            openview_source.build_command_list)

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

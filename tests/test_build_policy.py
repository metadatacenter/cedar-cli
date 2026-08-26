import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from CedarCliSettings import CedarCliSettings
from org.metadatacenter import build
from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
from org.metadatacenter.util.GlobalContext import GlobalContext


class BuildPolicyTest(unittest.TestCase):

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
            self.assertIn("--tests", result.output)
            self.assertIn("--skip-tests", result.output)

        for command in ("frontends", "split-frontends"):
            result = self.runner.invoke(build.app, [command, "--help"])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertNotIn("--skip-tests", result.output)

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

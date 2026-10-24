from contextlib import ExitStack, nullcontext
import re
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from org.metadatacenter.util.InvocationContext import InvocationContext, use_context, current_context
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
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(use_context(InvocationContext(environment=os.environ)))
        # Plan-only fixtures point CEDAR_HOME at a synthetic path. Supply the real
        # recipe data explicitly rather than weakening missing-inventory validation.
        from org.metadatacenter.frontend_inventory import inventory
        recipes = inventory()
        contexts.enter_context(patch('org.metadatacenter.frontend_inventory.inventory', return_value=recipes))
        GlobalContext.init_task_operators()
        self.runner = CliRunner()

    @staticmethod
    def commands(plan, repository=None):
        result = []
        for task in plan.tasks:
            if task.command_list and (repository is None or getattr(getattr(task, "repo", None), "name", None) == repository):
                result.extend(task.command_list)
            result.extend(BuildPolicyTest.commands(task, repository))
        return result

    def test_release_policy_import_does_not_require_a_populated_workspace(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([sys.executable, '-c',
                'from org.metadatacenter.release_support import policy'],
                env={**os.environ, 'CEDAR_HOME':home}, text=True, capture_output=True)
        self.assertEqual(0,result.returncode,result.stderr)

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
    def test_maven_threads_follow_the_host_unless_jobs_is_given(self, execute):
        for cpus, args, threads, jobs in (
                (16, [], 16, 2), (4, [], 4, 2), (32, [], 16, 2), (1, [], 1, 2), (None, [], 2, 2),
                (16, ["--jobs", "3"], 3, 3), (16, ["--jobs", "1"], 1, 1)):
            with patch("org.metadatacenter.util.InvocationContext.os.cpu_count", return_value=cpus):
                result = self.runner.invoke(build.app, args + ["java", "--dry-run"])
            self.assertEqual(0, result.exit_code, result.output)
            self.assertEqual(jobs, current_context().settings.build_jobs)
            self.assertEqual(threads, current_context().settings.maven_threads)
            maven_commands = [command for command in self.commands(execute.call_args.args[0])
                              if command.startswith("./mvnw clean install")]
            self.assertTrue(maven_commands)
            expected = f"./mvnw clean install -T {threads}" if threads > 1 else "./mvnw clean install"
            self.assertTrue(all(command == expected for command in maven_commands),
                            (cpus, args, maven_commands))

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

    @patch.object(build.plan_executor, "execute")
    def test_all_skip_tests_keeps_builds_but_omits_frontend_verification(self, execute):
        for skipped in (False, True):
            args = ["all", "--dry-run"] + (["--skip-tests"] if skipped else [])
            result = self.runner.invoke(build.app, args)
            self.assertEqual(0, result.exit_code, result.output)
            commands = self.commands(execute.call_args.args[0])
            self.assertIn("npm run build", commands)
            self.assertIn("npm ci", commands)
            model_commands = self.commands(execute.call_args.args[0], "cedar-model-typescript-library")
            self.assertEqual(not skipped, "npm run test:ci" in model_commands)
            maven = [c for c in commands if c.startswith("./mvnw clean install")]
            self.assertTrue(maven)
            self.assertTrue(all(("-DskipTests" in c) == skipped for c in maven))
        # A previous compile-only command must not weaken the full reactor contract.
        result = self.runner.invoke(build.app, ["frontends", "--dry-run"])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("npm run test:ci", self.commands(execute.call_args.args[0], "cedar-model-typescript-library"))

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

    def test_coverage_is_reported_only_after_a_build_that_ran_tests(self):
        for args, reported in ((["java"], True), (["parent"], True),
                               (["java", "--skip-tests"], False), (["java", "--dry-run"], False)):
            with patch.object(build, "execute_build") as execute, \
                    patch.object(build, "require_integration_tags"), \
                    patch.object(build, "report_coverage") as report:
                result = self.runner.invoke(build.app, args)
            self.assertEqual(0, result.exit_code, result.output)
            execute.assert_called_once()
            self.assertEqual(reported, report.called, args)

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

    @patch.object(build.plan_executor, "execute")
    def test_monitoring_and_bridging_check_built_browser_surfaces(self, execute):
        result = self.runner.invoke(build.app, ["frontends", "--dry-run"])
        self.assertEqual(0, result.exit_code, result.output)
        for repository in ('cedar-monitoring-src', 'cedar-bridging-src'):
            with self.subTest(repository=repository):
                commands = self.commands(execute.call_args.args[0], repository)
                browser = commands.index('npm --prefix ../browser test')
                compiled = commands.index('npm run build')
                self.assertLess(commands.index('npm --prefix ../browser ci'), compiled)
                self.assertLess(commands.index('../browser/node_modules/.bin/playwright install --with-deps chromium'), browser)
                self.assertLess(commands.index('npm test -- --watch=false'), compiled)
                self.assertLess(compiled, browser)

    def test_retired_typescript_roundtrip_is_not_registered(self):
        repos = ReposFactory.build_repos()

        self.assertNotIn("cedar-model-typescript-library-roundtrip", repos.map)
        self.assertNotIn(
            "cedar-model-typescript-library-roundtrip",
            [repo.name for repo in repos.get_frontends()],
        )

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
        home = Path("/tmp").resolve()
        built = Plan("guarded")
        built.tasks.append(SimpleNamespace(repo=SimpleNamespace(name="cedar-example", parent_repo=None),
                                           tasks=[]))
        baseline = {home / "cedar-example": b"pre-existing diff"}
        changed = {home / "cedar-example": b"build-generated diff"}
        with patch.object(build, "capture_estate_state", side_effect=[baseline, changed]), \
                patch.object(build.plan_executor, "execute"), \
                patch.object(Util, "cedar_home", "/tmp"):
            with self.assertRaises(SystemExit) as raised:
                build.execute_build(built, dry_run=False, dump_plan=False)

        self.assertEqual(1, raised.exception.code)

    def test_a_java_build_ignores_a_change_in_a_repository_it_does_not_build(self):
        # The invariant covers the inputs of the build, not the whole estate, for Java as for the
        # frontends: another session's edit to an unrelated repository is not this build's doing.
        home = Path("/tmp").resolve()
        baseline = {home / "cedar-embeddable-designer": b"before"}
        changed = {home / "cedar-embeddable-designer": b"edited elsewhere"}
        with patch.object(build, "capture_estate_state", side_effect=[baseline, changed]), \
                patch.object(build.plan_executor, "execute"), \
                patch.object(Util, "cedar_home", "/tmp"):
            build.execute_build(Plan("guarded"), dry_run=False, dump_plan=False)

    @staticmethod
    def aggregator_estate(home: Path):
        """cedar-project builds two sibling checkouts, one of which aggregates a third."""
        def pom(checkout, *modules, commented=()):
            listed = "".join(f"<module>{module}</module>" for module in modules)
            hidden = "".join(f"<!-- <module>{module}</module> -->" for module in commented)
            (home / checkout).mkdir(parents=True, exist_ok=True)
            (home / checkout / "pom.xml").write_text(
                '<project xmlns="http://maven.apache.org/POM/4.0.0">'
                f"<modules>{listed}{hidden}</modules>"
                "<profiles><profile><modules><module>../cedar-profile-only</module></modules>"
                "</profile></profiles></project>")
        pom("cedar-project", "../cedar-artifact-server", "../cedar-microservice-libraries",
            "cedar-project-internal", commented=("../cedar-retired-server",))
        pom("cedar-microservice-libraries", "cedar-search-operations-library",
            "../cedar-model-library/cedar-model-library-core")
        pom("cedar-artifact-server", "cedar-artifact-server-application")
        plan = Plan("Build project")
        plan.tasks.append(SimpleNamespace(repo=SimpleNamespace(name="cedar-project", parent_repo=None),
                                          tasks=[]))
        return plan

    def test_an_aggregator_build_reads_the_checkouts_its_modules_name(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plan = self.aggregator_estate(home)
            with patch.object(Util, "cedar_home", directory):
                roots = build.build_input_roots(plan)

        # Commented-out and profile-only modules are not built by default, so they are not inputs.
        self.assertEqual({home / name for name in (
            "cedar-cli", "cedar-development", "cedar-project", "cedar-artifact-server",
            "cedar-microservice-libraries", "cedar-model-library")}, roots)

    def test_a_java_build_fails_when_a_checkout_an_aggregator_builds_changes(self):
        # cedar-project compiles cedar-artifact-server through a ../ module, so an edit there
        # during the build changes what the build read.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            plan = self.aggregator_estate(home)
            baseline = {home / "cedar-artifact-server": b"before"}
            changed = {home / "cedar-artifact-server": b"edited elsewhere"}
            with patch.object(build, "capture_estate_state", side_effect=[baseline, changed]), \
                    patch.object(build.plan_executor, "execute"), \
                    patch.object(Util, "cedar_home", directory):
                with self.assertRaises(SystemExit) as raised:
                    build.execute_build(plan, dry_run=False, dump_plan=False)

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

    def test_frontend_worker_budget_overrides_inherited_runner_limits(self):
        executor = ShellTaskExecutor()
        task = SimpleNamespace(
            node_id=3, repo=SimpleNamespace(name="frontend", repo_type="TYPESCRIPT"),
            command_list=["npm run test:ci"],
            get_parameter=lambda name: name == "isolated_frontend_build",
        )
        current_context().settings.build_workers = 3
        module = "org.metadatacenter.taskexecutor.ShellTaskExecutor"
        with ExitStack() as stack:
            stack.enter_context(patch(module + ".Util.get_wd", return_value="/tmp/source"))
            stack.enter_context(patch(module + ".is_frontend_build", return_value=False))
            stack.enter_context(patch(module + ".reactor_evidence.begin"))
            stack.enter_context(patch(module + ".reactor.resolve", return_value=[]))
            stack.enter_context(patch(module + ".reactor.prepare_checks", return_value=[]))
            stack.enter_context(patch(module + ".isolated_frontend_workspace", return_value=
                nullcontext((Path("/tmp/copy"), {"VITEST_MAX_WORKERS": "99"}, []))))
            execute = stack.enter_context(patch.object(executor, "_execute_commands", return_value=1))
            self.assertEqual(1, executor.execute_shell_command_list(task, Mock(), dry_run=False))
        environment = execute.call_args.args[-1]
        for variable in ("CEDAR_TEST_WORKERS", "NG_BUILD_MAX_WORKERS", "VITEST_MAX_WORKERS"):
            self.assertEqual("3", environment[variable])

    def test_nested_browser_failure_retains_sibling_reports_and_command_log(self):
        executor = ShellTaskExecutor()
        task = SimpleNamespace(
            node_id=3, repo=SimpleNamespace(name='frontend-src', repo_type='TYPESCRIPT'),
            command_list=['npm --prefix ../browser test'],
            get_parameter=lambda name: name == 'isolated_frontend_build',
        )
        module = 'org.metadatacenter.taskexecutor.ShellTaskExecutor'
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            home = Path(directory)
            copy = home / 'copy'
            app = copy / 'frontend-src'
            app.mkdir(parents=True)
            report = copy / 'browser/test-results/failure.txt'
            report.parent.mkdir(parents=True)
            report.write_text('browser failure')
            environment = {'CEDAR_BUILD_WORKSPACE_ROOT': str(copy)}
            stack.enter_context(use_context(InvocationContext(environment={**os.environ, 'CEDAR_HOME': directory})))
            stack.enter_context(patch(module + '.Util.get_wd', return_value='/tmp/source/frontend-src'))
            stack.enter_context(patch(module + '.is_frontend_build', return_value=False))
            stack.enter_context(patch(module + '.reactor_evidence.begin'))
            stack.enter_context(patch(module + '.reactor.resolve', return_value=[]))
            stack.enter_context(patch(module + '.reactor.prepare_checks', return_value=[]))
            stack.enter_context(patch(module + '.isolated_frontend_workspace', return_value=
                nullcontext((app, environment, []))))

            def fail(_task, _repo, _commands, _cwd, _progress, child_env):
                Path(child_env['CEDAR_BUILD_DIAGNOSTIC_LOG']).write_text('failed command')
                return 1

            stack.enter_context(patch.object(executor, '_execute_commands', side_effect=fail))
            self.assertEqual(1, executor.execute_shell_command_list(task, Mock(), dry_run=False))
            bundles = list((home / '.cedar/build-reports/failures').iterdir())
            self.assertEqual(1, len(bundles))
            self.assertEqual('browser failure', (bundles[0] / 'browser/test-results/failure.txt').read_text())
            self.assertEqual('failed command', (bundles[0] / '.cedar-build-commands.log').read_text())

    def test_test_bearing_maven_task_checks_for_mongods_before_and_after(self):
        executor = ShellTaskExecutor()
        task = SimpleNamespace(
            node_id=3,
            repo=SimpleNamespace(name="cedar-example", repo_type="JAVA"),
            command_list=["./mvnw clean install"],
        )

        with tempfile.TemporaryDirectory() as home, \
                use_context(InvocationContext(environment={**os.environ, 'CEDAR_HOME': home})), \
                patch("org.metadatacenter.taskexecutor.ShellTaskExecutor.Util.get_wd",
                   return_value="/tmp"), \
                patch.object(executor, "execute_shell_command", return_value=([], 0)) as execute, \
                patch(
                    "org.metadatacenter.taskexecutor.ShellTaskExecutor."
                    "require_no_embedded_mongo_processes") as before, \
                patch(
                    "org.metadatacenter.taskexecutor.ShellTaskExecutor."
                    "wait_for_no_embedded_mongo_processes") as after:
            return_code = executor.execute_shell_command_list(
                task, Mock(), dry_run=False)

        self.assertEqual(0, return_code)
        child_env = execute.call_args.kwargs['environment']
        self.assertIn(child_env['TMPDIR'], child_env['JAVA_TOOL_OPTIONS'])
        self.assertFalse(Path(child_env['TMPDIR']).exists())
        before.assert_called_once_with(
            "test-bearing Maven task for cedar-example")
        after.assert_called_once_with(
            "completion of test-bearing Maven task for cedar-example")

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

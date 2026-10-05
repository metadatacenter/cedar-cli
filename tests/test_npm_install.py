import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from org.metadatacenter import npm_install
from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
from org.metadatacenter.util.GlobalContext import GlobalContext

DROPPED = "21 verbose reify failed optional dependency {}/node_modules/@esbuild/darwin-arm64\n"
CLEAN = "19 http fetch GET 200 https://registry.npmjs.org/esbuild/-/esbuild-0.28.2.tgz 99ms\n"


def write_log(cache, name, text):
    logs = Path(cache) / "_logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"2026-10-05T03_22_{name}Z-debug-0.log").write_text(text)


class InstallCommandTest(unittest.TestCase):
    def test_every_install_shape_the_frontends_use(self):
        for command in ("npm ci", "npm install", "npm --prefix visual ci",
                        "npm --prefix browser install", "npm install --legacy-peer-deps"):
            self.assertTrue(npm_install.is_install(command), command)

    def test_scripts_and_other_installers_are_not_npm_installs(self):
        for command in ("npm run ci", "npm --prefix visual run install", "npm run test:ci",
                        "./browser/node_modules/.bin/playwright install --with-deps chromium",
                        "npx playwright install"):
            self.assertFalse(npm_install.is_install(command), command)


class DroppedOptionalDependencyTest(unittest.TestCase):
    def test_reads_only_logs_written_since_the_snapshot(self):
        with tempfile.TemporaryDirectory() as cache:
            write_log(cache, "00_000", DROPPED.format("/old"))
            before = npm_install.debug_logs(cache)
            write_log(cache, "22_039", CLEAN + DROPPED.format("/build/repo"))
            self.assertEqual(["@esbuild/darwin-arm64"],
                             npm_install.dropped_optional_dependencies(cache, before))

    def test_a_nested_package_is_reported_by_its_name(self):
        with tempfile.TemporaryDirectory() as cache:
            write_log(cache, "22_039",
                      "21 verbose reify failed optional dependency "
                      "/b/node_modules/vite/node_modules/@rollup/rollup-darwin-arm64\n")
            self.assertEqual(["@rollup/rollup-darwin-arm64"],
                             npm_install.dropped_optional_dependencies(cache, set()))

    def test_a_missing_log_directory_reports_nothing(self):
        with tempfile.TemporaryDirectory() as cache:
            self.assertEqual(set(), npm_install.debug_logs(cache))
            self.assertEqual([], npm_install.dropped_optional_dependencies(cache, set()))
        self.assertIsNone(npm_install.log_directory(None))


class ExecutorReinstallTest(unittest.TestCase):
    """An install that drops an optional dependency runs once more, and fails if it drops it again."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cache = Path(temp.name) / "npm-cache"
        self.command_log = Path(temp.name) / ".cedar-build-commands.log"
        self.environment = {"npm_config_cache": str(self.cache),
                            "CEDAR_BUILD_DIAGNOSTIC_LOG": str(self.command_log)}
        self.task = SimpleNamespace(node_id=1, repo=SimpleNamespace(name="cedar-example"))
        self.executor = ShellTaskExecutor()

    def run_commands(self, commands, attempts, environment=None):
        """Each attempt is (exit code, whether npm's log reports a dropped optional dependency)."""
        attempts = iter(attempts)

        def execute(task, repo, command, cwd, job_progress, environment=None):
            code, dropped = next(attempts)
            write_log(self.cache, f"{execute.calls:02d}_000",
                      CLEAN + (DROPPED.format("/build") if dropped else ""))
            execute.calls += 1
            return [], code
        execute.calls = 0
        with patch.object(GlobalContext, "fail_on_error", return_value=True), \
                patch.object(self.executor, "execute_shell_command", side_effect=execute) as shell:
            code = self.executor._execute_commands(
                self.task, self.task.repo, commands, "/build/repo", Mock(),
                self.environment if environment is None else environment)
        return code, shell.call_count

    def test_a_failed_postinstall_after_a_drop_is_installed_again(self):
        self.assertEqual((0, 2), self.run_commands(["npm install"], [(1, True), (0, False)]))
        self.assertIn("so the install runs again", self.command_log.read_text())

    def test_a_drop_behind_a_successful_install_is_installed_again(self):
        self.assertEqual((0, 2), self.run_commands(["npm ci"], [(0, True), (0, False)]))

    def test_a_second_drop_fails_the_install_even_when_npm_succeeds(self):
        self.assertEqual((1, 2), self.run_commands(["npm --prefix visual install"],
                                                   [(0, True), (0, True)]))
        self.assertIn("@esbuild/darwin-arm64 again", self.command_log.read_text())

    def test_any_other_install_failure_is_not_retried(self):
        self.assertEqual((1, 1), self.run_commands(["npm install"], [(1, False)]))
        self.assertFalse(self.command_log.exists())

    def test_commands_that_are_not_installs_run_once(self):
        self.assertEqual((0, 1), self.run_commands(["npm run build"], [(0, True)]))

    def test_a_build_without_its_own_npm_cache_runs_an_install_once(self):
        self.assertEqual((0, 1), self.run_commands(["npm install"], [(0, True)], environment={}))


if __name__ == "__main__":
    unittest.main()

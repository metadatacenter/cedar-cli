import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from org.metadatacenter import build_scheduler, npm_install
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
            build_scheduler.record_timing(repo.name, command, time.monotonic(), code)
            return [], code
        execute.calls = 0
        self.records = []
        token = build_scheduler._timings.set(self.records)
        try:
            with patch.object(GlobalContext, "fail_on_error", return_value=True), \
                    patch.object(self.executor, "execute_shell_command", side_effect=execute) as shell:
                code = self.executor._execute_commands(
                    self.task, self.task.repo, commands, "/build/repo", Mock(),
                    self.environment if environment is None else environment)
        finally:
            build_scheduler._timings.reset(token)
        return code, shell.call_count

    def dropped_per_attempt(self):
        return [record.get("droppedOptionalDependencies") for record in self.records]

    def test_a_failed_postinstall_after_a_drop_is_installed_again(self):
        self.assertEqual((0, 2), self.run_commands(["npm install"], [(1, True), (0, False)]))
        self.assertIn("so the install runs again", self.command_log.read_text())
        self.assertEqual([["@esbuild/darwin-arm64"], None], self.dropped_per_attempt())

    def test_a_drop_behind_a_successful_install_is_installed_again(self):
        self.assertEqual((0, 2), self.run_commands(["npm ci"], [(0, True), (0, False)]))

    def test_a_second_drop_fails_the_install_even_when_npm_succeeds(self):
        self.assertEqual((1, 2), self.run_commands(["npm --prefix visual install"],
                                                   [(0, True), (0, True)]))
        self.assertIn("@esbuild/darwin-arm64 again", self.command_log.read_text())
        self.assertEqual([["@esbuild/darwin-arm64"]] * 2, self.dropped_per_attempt())

    def test_any_other_install_failure_is_not_retried(self):
        self.assertEqual((1, 1), self.run_commands(["npm install"], [(1, False)]))
        self.assertFalse(self.command_log.exists())
        self.assertEqual([None], self.dropped_per_attempt())

    def test_commands_that_are_not_installs_run_once(self):
        self.assertEqual((0, 1), self.run_commands(["npm run build"], [(0, True)]))

    def test_a_build_without_its_own_npm_cache_runs_an_install_once(self):
        self.assertEqual((0, 1), self.run_commands(["npm install"], [(0, True)], environment={}))



def logs_dir_of(command):
    return Path(next(arg for arg in command if arg.startswith("--logs-dir=")).split("=", 1)[1])


class InstallTest(unittest.TestCase):
    """An install outside the build executor, under the same rule, reading only its own log."""

    def attempts(self, outcomes, failure=RuntimeError):
        """Each outcome is (whether the attempt raises, whether its log reports a drop)."""
        outcomes = iter(outcomes)
        commands, notes = [], []

        def run(command):
            commands.append(command)
            raises, dropped = next(outcomes)
            (logs_dir_of(command) / "2026-10-06T03_00_00_000Z-debug-0.log").write_text(
                CLEAN + (DROPPED.format("/repo") if dropped else ""))
            if raises:
                raise subprocess.CalledProcessError(1, command)

        npm_install.install(run, ["npm", "ci"], notes.append, failure)
        return commands, notes

    def test_a_clean_install_runs_once_with_a_log_directory_of_its_own(self):
        commands, notes = self.attempts([(False, False)])
        self.assertEqual(1, len(commands))
        self.assertEqual(["npm", "ci"], commands[0][:2])
        self.assertFalse(logs_dir_of(commands[0]).exists(), "the log directory is removed afterwards")
        self.assertEqual([], notes)

    def test_a_drop_runs_the_install_again_whether_or_not_npm_failed(self):
        for raises in (False, True):
            commands, notes = self.attempts([(raises, True), (False, False)])
            self.assertEqual(2, len(commands))
            self.assertNotEqual(logs_dir_of(commands[0]), logs_dir_of(commands[1]))
            self.assertIn("so the install runs again", notes[0])

    def test_a_second_drop_fails_with_the_callers_error(self):
        with self.assertRaises(KeyError) as raised:
            self.attempts([(False, True), (False, True)], failure=KeyError)
        self.assertIn("@esbuild/darwin-arm64 again", str(raised.exception))

    def test_a_failure_with_nothing_dropped_is_raised_as_it_was_and_not_retried(self):
        outcomes = iter([(True, False)])
        calls = []

        def run(command):
            calls.append(command)
            raise subprocess.CalledProcessError(7, command)

        with self.assertRaises(subprocess.CalledProcessError) as raised:
            npm_install.install(run, ["npm", "install"], lambda _m: None)
        self.assertEqual(7, raised.exception.returncode)
        self.assertEqual(1, len(calls))


class CommandLineTest(unittest.TestCase):
    """The entry point a shell script calls, run against a stand-in for npm."""

    def fake_npm(self, directory, script):
        npm = Path(directory) / "npm"
        npm.write_text("#!/bin/sh\n" + script)
        npm.chmod(0o755)
        return {"PATH": f"{directory}:/usr/bin:/bin"}

    def run_main(self, script):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fake_npm(directory, script)
            with patch.dict("os.environ", path):
                return npm_install.main(["npm_install.py", "npm", "ci", "--no-audit"])

    def test_a_clean_install_exits_zero(self):
        self.assertEqual(0, self.run_main("exit 0\n"))

    def test_npm_failing_without_a_drop_passes_its_exit_code_through(self):
        self.assertEqual(3, self.run_main("exit 3\n"))

    def test_a_drop_on_every_attempt_exits_one(self):
        log_drop = ('for arg in "$@"; do case "$arg" in --logs-dir=*) dir="${arg#--logs-dir=}";; esac; done\n'
                    'echo "21 verbose reify failed optional dependency /r/node_modules/@esbuild/x" '
                    '> "$dir/2026-10-06T03_00_00_000Z-debug-0.log"\nexit 0\n')
        self.assertEqual(1, self.run_main(log_drop))

    def test_anything_but_an_npm_install_is_refused(self):
        self.assertEqual(2, npm_install.main(["npm_install.py", "npm", "run", "build"]))
        self.assertEqual(2, npm_install.main(["npm_install.py"]))


if __name__ == "__main__":
    unittest.main()

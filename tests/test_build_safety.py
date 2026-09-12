import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError,
    _process_cwd,
    capture_estate_state,
    changed_repositories,
    embedded_mongo_processes,
    is_test_bearing_maven_command,
    isolated_frontend_workspace,
    require_no_frontend_runtime_collision,
)
from org.metadatacenter.util.SubprocessDiagnostics import (
    describe_return_code,
    describe_subprocess_failure,
)


class BuildSafetyTest(unittest.TestCase):
    @staticmethod
    def repository(root: Path) -> Path:
        repo = root / "cedar-example"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.org"],
            check=True,
        )
        (repo / "source.txt").write_text("source\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "source.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "fixture"], check=True)
        return repo

    def test_isolated_build_has_private_cache_and_preserves_preexisting_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = self.repository(Path(directory))
            (repo / "source.txt").write_text("developer change\n", encoding="utf-8")
            with patch('org.metadatacenter.util.BuildSafety.invocation_environment',
                       return_value={**os.environ, 'CEDAR_HOME': directory}), patch(
                "org.metadatacenter.util.BuildSafety.frontend_runtime_collisions",
                return_value=[(42, "ng serve")],
            ):
                with isolated_frontend_workspace(repo) as (workspace, environment, collisions):
                    self.assertNotEqual(repo, workspace)
                    self.assertEqual("developer change\n", (workspace / "source.txt").read_text())
                    (workspace / "source.txt").write_text("generated\n", encoding="utf-8")
                    self.assertEqual("true", environment["CI"])
                    self.assertTrue(environment["npm_config_cache"].startswith(str(workspace.parent)))
                    self.assertEqual(
                        str(workspace / "node_modules" / ".bin"),
                        environment["PATH"].split(os.pathsep)[0],
                    )
                    self.assertEqual([(42, "ng serve")], collisions)
            self.assertEqual("developer change\n", (repo / "source.txt").read_text())

    def test_estate_snapshot_detects_only_changes_after_the_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.repository(root)
            (repo / "source.txt").write_text("pre-existing\n", encoding="utf-8")
            before = capture_estate_state(root)
            self.assertEqual([], changed_repositories(before, capture_estate_state(root)))
            (repo / "source.txt").write_text("build changed it\n", encoding="utf-8")
            self.assertEqual([repo.resolve()], changed_repositories(before, capture_estate_state(root)))

    def test_in_place_build_refuses_a_live_runtime(self):
        with patch(
            "org.metadatacenter.util.BuildSafety.frontend_runtime_collisions",
            return_value=[(17, "npm run start")],
        ):
            with self.assertRaisesRegex(BuildSafetyError, "PID 17"):
                require_no_frontend_runtime_collision(Path("/tmp/frontend"))

    def test_embedded_mongo_inventory_excludes_the_native_server(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            embedded = root / "cache" / ".embedmongo" / "5.0" / "mongod"
            native = root / "homebrew" / "mongodb" / "mongod"
            embedded.parent.mkdir(parents=True)
            native.parent.mkdir(parents=True)
            embedded.touch()
            native.touch()
            proc = root / "proc"
            (proc / "42").mkdir(parents=True)
            (proc / "84").mkdir(parents=True)
            (proc / "42" / "exe").symlink_to(embedded)
            (proc / "84" / "exe").symlink_to(native)

            def commands(*_args, **_kwargs):
                return SimpleNamespace(
                    returncode=0, stdout="n127.0.0.1:42317\n", stderr="")

            processes = embedded_mongo_processes(
                command_runner=commands, proc_root=proc)

        self.assertEqual([42], [process.pid for process in processes])
        self.assertEqual(("127.0.0.1:42317",), processes[0].listeners)

    def test_only_test_bearing_maven_commands_get_the_process_gate(self):
        self.assertTrue(is_test_bearing_maven_command("./mvnw clean install"))
        self.assertTrue(is_test_bearing_maven_command("/tmp/repo/mvnw verify"))
        self.assertFalse(is_test_bearing_maven_command(
            "./mvnw clean install -DskipTests"))
        self.assertTrue(is_test_bearing_maven_command(
            "./mvnw clean install -DskipTests=false"))
        self.assertFalse(is_test_bearing_maven_command("npm test"))

    def test_macos_inventory_uses_the_executable_path_not_the_process_name(self):
        def commands(args, **_kwargs):
            if "txt" in args:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "p42\ncmongod\nftxt\n"
                        "n/Users/test/.embedmongo/5.0/mongod\n"
                        "p84\ncmongod\nftxt\n"
                        "n/opt/homebrew/opt/mongodb-community/bin/mongod\n"
                    ),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0, stdout="n127.0.0.1:42317\n", stderr="")

        processes = embedded_mongo_processes(
            command_runner=commands, proc_root=Path("/not-a-real-proc"))

        self.assertEqual([42], [process.pid for process in processes])
        self.assertEqual(
            Path("/Users/test/.embedmongo/5.0/mongod"),
            processes[0].executable,
        )

    def test_process_cwd_reads_a_real_platform_process(self):
        """Exercise Linux /proc and the macOS lsof fallback on their real runners."""
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                ["python3", "-c", "import time; time.sleep(30)"],
                cwd=directory,
            )
            try:
                self.assertEqual(Path(directory).resolve(), _process_cwd(process.pid))
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_ci_exercises_process_safety_on_linux_and_macos(self):
        workflow = (
            Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("macos-latest", workflow)

    def test_negative_return_code_names_signal_and_crash_evidence(self):
        self.assertEqual("exited with code 7", describe_return_code(7))
        self.assertEqual("was terminated by SIGABRT (signal 6)", describe_return_code(-6))
        with patch("platform.system", return_value="Darwin"), \
                patch("pathlib.Path.home", return_value=Path("/Users/test")):
            detail = describe_subprocess_failure(-6)
        self.assertIn("SIGABRT (signal 6)", detail)
        self.assertIn("/Users/test/Library/Logs/DiagnosticReports", detail)

    def test_crash_evidence_points_at_the_place_this_host_keeps_it(self):
        """A signal leaves its evidence somewhere different on each system, and saying the wrong
        place is worse than saying nothing: it sends the reader to an empty directory."""
        with patch("platform.system", return_value="Linux"):
            linux = describe_subprocess_failure(-11)
        self.assertIn("SIGSEGV (signal 11)", linux)
        self.assertIn("coredumpctl", linux)
        self.assertNotIn("DiagnosticReports", linux)

        with patch("platform.system", return_value="SunOS"):
            unknown = describe_subprocess_failure(-6)
        self.assertIn("crash-report or core-dump location", unknown)

    def test_a_clean_exit_carries_no_crash_evidence_hint(self):
        for system in ("Darwin", "Linux", "SunOS"):
            with patch("platform.system", return_value=system):
                self.assertEqual("exited with code 0", describe_subprocess_failure(0))

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError, executable_build_workspace, isolated_frontend_workspace,
)
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context


class BuildTempTest(unittest.TestCase):
    def test_private_independent_workspaces_preserve_environment_and_clean_up(self):
        with tempfile.TemporaryDirectory(prefix="cedar temp ") as home:
            original = {**os.environ, "CEDAR_HOME": home, "TMPDIR": "/unusable",
                        "JAVA_TOOL_OPTIONS": "-Dexisting=value"}
            with executable_build_workspace(original, java=True) as (first, env):
                self.assertEqual(0o700, first.stat().st_mode & 0o777)
                self.assertTrue(first.is_relative_to(Path(home).resolve()))
                self.assertEqual("/unusable", original["TMPDIR"])
                self.assertIn("-Dexisting=value", env["JAVA_TOOL_OPTIONS"])
                self.assertIn(env["TMPDIR"], env["JAVA_TOOL_OPTIONS"])
                with executable_build_workspace(original) as (second, _):
                    self.assertNotEqual(first, second)
                self.assertFalse(second.exists())
            self.assertFalse(first.exists())

    def test_failure_and_interrupt_clean_up(self):
        with tempfile.TemporaryDirectory() as home:
            for error in (RuntimeError("build failed"), KeyboardInterrupt()):
                with self.assertRaises(type(error)):
                    with executable_build_workspace({"CEDAR_HOME": home}) as (path, _):
                        raise error
                self.assertFalse(path.exists())

    def test_noexec_fails_before_yield_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as home:
            with patch("org.metadatacenter.util.BuildSafety.subprocess.run",
                       side_effect=PermissionError("noexec")):
                with self.assertRaisesRegex(BuildSafetyError, "CEDAR_BUILD_TMPDIR"):
                    with executable_build_workspace({"CEDAR_HOME": home}):
                        self.fail("must not start build")
            self.assertEqual([], list((Path(home) / ".cedar/build-tmp").iterdir()))

    def test_override_and_conflicting_java_setting(self):
        with tempfile.TemporaryDirectory() as root:
            env = {"CEDAR_BUILD_TMPDIR": root, "MAVEN_OPTS": "-Djava.io.tmpdir=/tmp"}
            with self.assertRaisesRegex(BuildSafetyError, "MAVEN_OPTS"):
                with executable_build_workspace(env, java=True):
                    self.fail("conflicting override accepted")
            env.pop("MAVEN_OPTS")
            with executable_build_workspace(env) as (path, _):
                self.assertEqual(Path(root).resolve(), path.parent)

    def test_frontend_copy_and_tools_share_executable_storage(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            source.mkdir()
            (source / "file").write_text("source")
            with use_context(InvocationContext(environment={**os.environ, "CEDAR_HOME": root})), \
                    patch("org.metadatacenter.util.BuildSafety.repository_root", return_value=None), \
                    patch("org.metadatacenter.util.BuildSafety.frontend_runtime_collisions", return_value=[]):
                with isolated_frontend_workspace(source) as (path, env, _):
                    self.assertTrue(Path(env["TMPDIR"]).is_relative_to(path.parent))
                    self.assertEqual("source", (path / "file").read_text())
                self.assertFalse(path.exists())

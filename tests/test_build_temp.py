import contextlib
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError, executable_build_workspace, isolated_frontend_workspace,
    socket_safe_root_length,
)
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context


@contextlib.contextmanager
def short_home(prefix="cedar temp "):
    """A CEDAR_HOME that leaves room for the build's own Unix socket.

    The platform temporary directory is 48 characters on macOS, and a build scratch nested under
    one that long cannot hold a socket at all, so a case that asserts where the scratch lands has
    to name a short base rather than inherit the platform's.
    """
    with tempfile.TemporaryDirectory(prefix=prefix, dir="/tmp") as home:
        yield home


class BuildTempTest(unittest.TestCase):
    def test_private_independent_workspaces_preserve_environment_and_clean_up(self):
        with short_home() as home:
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
        with short_home() as home:
            with patch("org.metadatacenter.util.BuildSafety.subprocess.run",
                       side_effect=PermissionError("noexec")):
                with self.assertRaisesRegex(BuildSafetyError, "CEDAR_BUILD_TMPDIR"):
                    with executable_build_workspace({"CEDAR_HOME": home}):
                        self.fail("must not start build")
            self.assertEqual([], list((Path(home) / ".cedar/build-tmp").iterdir()))

    def test_override_and_conflicting_java_setting(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as root:
            env = {"CEDAR_BUILD_TMPDIR": root, "MAVEN_OPTS": "-Djava.io.tmpdir=/tmp"}
            with self.assertRaisesRegex(BuildSafetyError, "MAVEN_OPTS"):
                with executable_build_workspace(env, java=True):
                    self.fail("conflicting override accepted")
            env.pop("MAVEN_OPTS")
            with executable_build_workspace(env) as (path, _):
                self.assertEqual(Path(root).resolve(), path.parent)

    def test_a_build_can_open_its_database_socket_however_deep_the_home(self):
        # A release exports its own attempt workspace as CEDAR_HOME, and that path alone can
        # exceed what a Unix socket allows. The embedded MariaDB names its socket in TMPDIR, so
        # bind the same name rather than asserting the arithmetic that chose the directory.
        with short_home() as base:
            home = Path(base).joinpath(*(f"attempt-{index}" for index in range(6)))
            home.mkdir(parents=True)
            self.assertGreater(len(str(home)), socket_safe_root_length())
            with executable_build_workspace({"CEDAR_HOME": str(home), "HOME": base}) as (path, env):
                self.assertFalse(path.is_relative_to(home))
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.addCleanup(listener.close)
                listener.bind(str(Path(env["TMPDIR"]) / "MariaDB4j.65535.sock"))

    def test_a_home_that_fits_still_holds_its_own_build_scratch(self):
        with short_home() as home:
            with executable_build_workspace({"CEDAR_HOME": home, "HOME": "/unused"}) as (path, _):
                self.assertTrue(path.is_relative_to(Path(home).resolve()))

    def test_an_overlong_configured_root_is_refused_rather_than_replaced(self):
        with short_home(prefix="configured ") as base:
            root = Path(base).joinpath(*(f"segment-{index}" for index in range(6)))
            root.mkdir(parents=True)
            with self.assertRaisesRegex(BuildSafetyError, "CEDAR_BUILD_TMPDIR"):
                with executable_build_workspace({"CEDAR_BUILD_TMPDIR": str(root)}):
                    self.fail("a root too long for a socket was accepted")

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

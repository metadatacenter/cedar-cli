"""CEDAR release toolchain."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import os
import platform
import re
import subprocess
from org.metadatacenter.release_support.policy import (
    LINUX_JVM_ROOT,
    NODE_24_CANDIDATE_DIRECTORIES,
    REQUIRED_JAVA_MAJOR,
    REQUIRED_NODE_VERSION,
)


def java_17_remediation() -> str:
    """Advice an operator is meant to be able to run, so it has to suit the host giving it.

    macOS resolves a JDK through java_home. A Linux release host has no such tool, so name the
    location the CLI itself searches instead of a command that cannot work there.
    """
    if platform.system() == "Darwin":
        return "export JAVA_HOME=$(/usr/libexec/java_home -v 17)"
    return "export JAVA_HOME to a JDK 17, which on this host is usually one of /usr/lib/jvm/java-17-*"


def node_24_remediation() -> str:
    """The one line that puts the release's Node first on PATH, phrased for the host giving it."""
    wanted = REQUIRED_NODE_VERSION.removeprefix("v")
    if platform.system() == "Darwin":
        return f'export PATH="{NODE_24_CANDIDATE_DIRECTORIES[0]}:$PATH"'
    return f"put a Node {wanted} bin directory first on PATH, for example with nvm use {wanted}"


class ToolchainResolver:
    """Put the release's Java and Node first on PATH when the shell offers other versions.

    A developer shell pins whatever the day's work needs, and a release needs Java 17 and Node
    24.19.0 exactly. The runbook tells the operator to export both before starting; the CLI can
    follow those two instructions itself. It changes only the environment it is given, which for
    a command is its invocation and children, says what it substituted, and leaves the toolchain
    check to refuse whatever it could not find.
    """

    def __init__(self, environment, *, command_runner=None, system=None, exists=None, jvms=None):
        self.environment = environment
        self.command_runner = command_runner or subprocess.run
        self.system = system or platform.system()
        self.exists = exists or (lambda path: Path(path).is_file())
        self.jvms = jvms or (lambda: sorted(
            str(path) for path in Path(LINUX_JVM_ROOT).glob(f"*{REQUIRED_JAVA_MAJOR}*")))

    def _capture(self, args: list[str]) -> tuple[int, str, str]:
        try:
            result = self.command_runner(
                args, env=self.environment, text=True, capture_output=True, check=False)
        except OSError as error:
            return 127, "", str(error)
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()

    @staticmethod
    def _java_major(version_output: str) -> int | None:
        match = re.search(r'version "(\d+)', version_output)
        return int(match.group(1)) if match else None

    def _prepend_path(self, directory: str) -> None:
        current = self.environment.get("PATH", "")
        self.environment["PATH"] = f"{directory}{os.pathsep}{current}" if current else directory

    def resolve(self) -> list[str]:
        """Substitute what the release needs and report each substitution in one line."""
        return [*self._resolve_java(), *self._resolve_node()]

    def _resolve_java(self) -> list[str]:
        code, _, stderr = self._capture(["java", "-version"])
        major = self._java_major(stderr) if code == 0 else None
        if major == REQUIRED_JAVA_MAJOR:
            return []
        home = self._java_17_home()
        if not home:
            return []
        self.environment["JAVA_HOME"] = home
        self._prepend_path(str(Path(home) / "bin"))
        offered = f"Java {major}" if major else "no working java"
        return [f"Java {REQUIRED_JAVA_MAJOR} from {home}; the shell offered {offered}"]

    def _java_17_home(self) -> str | None:
        if self.system == "Darwin":
            code, home, _ = self._capture(
                ["/usr/libexec/java_home", "-v", str(REQUIRED_JAVA_MAJOR)])
            candidates = [home] if code == 0 and home else []
        else:
            candidates = list(self.jvms())
        for candidate in candidates:
            java = str(Path(candidate) / "bin" / "java")
            if not self.exists(java):
                continue
            code, _, stderr = self._capture([java, "-version"])
            if code == 0 and self._java_major(stderr) == REQUIRED_JAVA_MAJOR:
                return candidate
        return None

    def _resolve_node(self) -> list[str]:
        code, version, _ = self._capture(["node", "--version"])
        if code == 0 and version == REQUIRED_NODE_VERSION:
            return []
        for directory in NODE_24_CANDIDATE_DIRECTORIES:
            binary = str(Path(directory) / "node")
            if not self.exists(binary):
                continue
            candidate_code, candidate_version, _ = self._capture([binary, "--version"])
            if candidate_code == 0 and candidate_version == REQUIRED_NODE_VERSION:
                self._prepend_path(directory)
                offered = f"Node {version}" if code == 0 and version else "no working node"
                return [f"Node {REQUIRED_NODE_VERSION} from {directory}; the shell offered {offered}"]
        return []

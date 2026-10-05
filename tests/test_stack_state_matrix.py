"""Every state a managed service can be in, put to every command that reads the stack's status.

`cedarcli native status` and the smoke gate both read the controller's status, and each decided
for itself what a row means. They disagreed. After a version bump the old processes stayed
healthy and their BINARY column read "—", so status raised no warning and the gate recorded a
pass for code the stack was not running. A container-owned service passed the gate with no
question asked about its commit.

The invariant: whatever the gate refuses, status warns about, so an operator sees why; and
whatever status warns about because the stack cannot stand for its source, the gate refuses.
Warnings about how a process is managed rather than what it runs, such as one started outside the
controller, do not refuse the gate.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from org.metadatacenter import smoke_gate
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker import ServerWorker as server_worker_module
from org.metadatacenter.worker.ServerWorker import ServerWorker

VERSION = "2.9.17-SNAPSHOT"
HEADER = "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors\n"

# One row per state, for the resource server unless the state belongs to a frontend.
# Each names what the gate must do and whether status must warn: (row, gate refuses, status warns).
STATES = {
    "current": ("resource\t101\t9007\tup\thealthy\tcurrent\t0", False, False),
    "stale": ("resource\t101\t9007\tup\thealthy\tSTALE\t0", True, True),
    "no jar for its version": ("resource\t101\t9007\tup\thealthy\tMISSING\t0", True, True),
    "jar older than its source": ("resource\t101\t9007\tup\thealthy\tcurrent\t0", True, True),
    "unmanaged": ("resource\t~101\t9007\tup\thealthy\tcurrent\t0", False, True),
    "foreign listener": ("resource\t!101\t9007\tup\thealthy\t-\t0", True, True),
    "container-owned": ("resource\tdocker\t9007\tinternal\tdocker\t-\t-", True, True),
    "starting": ("resource\t101\t9007\tup\tstarting\tcurrent\t0", True, True),
    "unhealthy": ("resource\t101\t9007\tup\tUNHEALTHY\tcurrent\t0", True, True),
    "down": ("resource\t-\t9007\tdown\tdown\t-\t0", True, True),
    "editor stale": ("ui-main\t201\t4200\tup\thealthy\tSTALE\t0", True, True),
}


class StackStateMatrix(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name).resolve()
        repository = self.home / "cedar-resource-server"
        target = repository / "cedar-resource-server-application" / "target"
        target.mkdir(parents=True)
        for command in (["init", "--initial-branch=develop"],
                        ["-c", "user.email=t@example.org", "-c", "user.name=Test",
                         "commit", "--allow-empty", "-m", "source"]):
            subprocess.run(["git", *command], cwd=repository, check=True, capture_output=True)
        self.jar = target / f"cedar-resource-server-application-{VERSION}.jar"
        self.jar.write_text("jar", encoding="utf-8")
        # Built after its source, unless a state says otherwise.
        later = time.time() + 3600
        os.utime(self.jar, (later, later))

    def tearDown(self):
        self.directory.cleanup()

    def gate(self, row: str) -> list[str]:
        def runner(args, **kwargs):
            if str(args[0]).endswith("cedar-services.sh"):
                return SimpleNamespace(returncode=0, stdout=HEADER + row + "\n", stderr="")
            return subprocess.run(args, **kwargs)
        return smoke_gate.stack_findings(self.home, runner)

    def status(self, row: str) -> list[str]:
        rows = ServerWorker.parse_native_status((HEADER + row + "\n").splitlines())
        printed: list[str] = []
        with patch.object(server_worker_module.console, "print",
                          side_effect=lambda value, *a, **k: printed.append(str(value))), \
                patch.object(Util, "cedar_home", str(self.home)):
            ServerWorker.print_summary(rows, [], {})
        return [line for line in printed if line.startswith("WARNING")]

    def check(self, state: str) -> None:
        row, refuses, warns = STATES[state]
        if state == "jar older than its source":
            os.utime(self.jar, (0, 0))
        with use_context(InvocationContext(environment={**os.environ, "CEDAR_VERSION": VERSION})):
            refusals = self.gate(row)
            warnings = self.status(row)
        self.assertEqual(refuses, bool(refusals), f"gate: {refusals}")
        self.assertEqual(warns, bool(warnings), f"status: {warnings}")


def cells() -> None:
    for state in STATES:
        setattr(StackStateMatrix, "test_" + state.replace(" ", "_").replace("-", "_"),
                lambda self, s=state: self.check(s))


cells()


class TheJarTheControllerRuns(unittest.TestCase):
    """Source currency is asked of the jar for the configured version, not the newest of any."""

    def test_a_newer_jar_of_another_version_does_not_stand_in(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            target = home / "cedar-resource-server" / "cedar-resource-server-application" / "target"
            target.mkdir(parents=True)
            (target / "cedar-resource-server-application-2.9.18-SNAPSHOT.jar").write_text("next")
            with use_context(InvocationContext(environment={**os.environ, "CEDAR_VERSION": VERSION})):
                self.assertIsNone(smoke_gate.application_jar(home, "resource"))
                running = target / f"cedar-resource-server-application-{VERSION}.jar"
                running.write_text("running")
                self.assertEqual(running, smoke_gate.application_jar(home, "resource"))


if __name__ == "__main__":
    unittest.main()

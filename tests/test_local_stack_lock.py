import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from org.metadatacenter.taskexecutor.ShellTaskExecutor import _free_port
from org.metadatacenter.util.local_stack_lock import local_stack

ROOT = Path(__file__).resolve().parents[1]

HOLDER = """
import sys, time
from org.metadatacenter.util.local_stack_lock import local_stack
with local_stack(sys.argv[1], report=lambda _line: None):
    print("held", flush=True)
    time.sleep(1.5)
"""


class LocalStackLockTest(unittest.TestCase):
    def test_a_process_takes_the_stack_again_without_waiting_for_itself(self):
        """The reactor's smoke is the smoke `test e2e` runs, so it takes the lock it already holds."""
        with tempfile.TemporaryDirectory() as home:
            reports = []
            with local_stack(home, reports.append):
                with local_stack(home, reports.append):
                    pass
            self.assertEqual([], reports)

    def test_a_second_process_waits_for_the_holder_and_names_it(self):
        with tempfile.TemporaryDirectory() as home:
            holder = subprocess.Popen([sys.executable, "-c", HOLDER, home], cwd=ROOT,
                                      stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual("held", holder.stdout.readline().strip())
                reports, asked = [], time.monotonic()
                with local_stack(home, reports.append):
                    # The holder keeps it for 1.5 s after saying so, and this waited that out.
                    self.assertGreater(time.monotonic() - asked, 1.0)
                self.assertEqual([f"Waiting for PID {holder.pid} to finish its local deployment or smoke."],
                                 reports)
            finally:
                holder.kill()
                holder.wait()
                holder.stdout.close()


class FreePortTest(unittest.TestCase):
    def test_a_copy_is_given_a_port_nothing_listens_on(self):
        port = _free_port()
        self.assertGreater(port, 0)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()

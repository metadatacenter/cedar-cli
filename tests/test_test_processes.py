import signal
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import test_processes
from org.metadatacenter.util.BuildSafety import EmbeddedMongoProcess


class TestProcessCommandsTest(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.process = EmbeddedMongoProcess(
            42,
            Path("/Users/test/.embedmongo/5.0/mongod"),
            ("127.0.0.1:42317",),
        )

    @patch.object(test_processes, "embedded_mongo_processes")
    def test_status_reports_the_exact_process_and_listener(self, inventory):
        inventory.return_value = [self.process]

        result = self.runner.invoke(test_processes.app, ["status"])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("PID 42", result.output)
        self.assertIn(".embedmongo/5.0/mongod", result.output)
        self.assertIn("127.0.0.1:42317", result.output)

    @patch.object(test_processes.os, "kill")
    @patch.object(test_processes, "embedded_mongo_processes")
    def test_cleanup_sends_sigterm_only_to_the_inventory(self, inventory, kill):
        inventory.side_effect = [[self.process], []]

        result = self.runner.invoke(test_processes.app, ["cleanup"])

        self.assertEqual(0, result.exit_code, result.output)
        kill.assert_called_once_with(42, signal.SIGTERM)
        self.assertIn("Stopped 1 embedded Mongo test process", result.output)


if __name__ == "__main__":
    unittest.main()

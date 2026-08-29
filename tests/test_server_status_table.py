import unittest
from io import StringIO
from unittest.mock import patch

from rich.console import Console

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.ServerWorker import ServerWorker


class ServerStatusTableTest(unittest.TestCase):

    def test_combined_table_keeps_native_operational_details(self):
        output = StringIO()
        status = [
            "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors",
            "resource\t33871\t9007\tup\thealthy\tSTALE\t3318",
            "worker\t~33967\t9011\tup\tUNHEALTHY\tcurrent\t7",
            "ui-main\t-\t4200\tdown\tdown\t-\t0",
        ]

        with patch.object(Util, "get_servers", return_value=[]), patch(
                "org.metadatacenter.worker.ServerWorker.console",
                Console(file=output, width=180, color_system=None)):
            ServerWorker.status(status)

        rendered = output.getvalue()
        self.assertIn("CEDAR native status", rendered)
        self.assertIn("PID", rendered)
        self.assertIn("Binary", rendered)
        self.assertIn("Log errors", rendered)
        self.assertIn("9007 up", rendered)
        self.assertIn("33871", rendered)
        self.assertIn("STALE", rendered)
        self.assertIn("3,318", rendered)
        self.assertIn("~33967", rendered)
        self.assertIn("worker (UNHEALTHY)", rendered)
        self.assertIn("native 1/3 healthy", rendered)
        self.assertNotIn("CEDAR native host and infrastructure status", rendered)

    def test_machine_status_schema_is_checked(self):
        with self.assertRaisesRegex(ValueError, "unexpected schema"):
            ServerWorker.parse_native_status(["SERVICE PID PORT"])


if __name__ == "__main__":
    unittest.main()

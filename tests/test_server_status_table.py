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

    def test_a_stale_editor_frontend_is_told_to_reinstall_not_restart(self):
        """A restart cannot fix an Editor the lock outran; only npm ci and the copy task can."""
        output = StringIO()
        status = [
            "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors",
            "resource\t33871\t9007\tup\thealthy\tSTALE\t0",
            "ui-main\t9202\t4200\tup\thealthy\tSTALE\t0",
            "ui-workspace\t9252\t4201\tup\thealthy\tSTALE\t0",
            "ui-designer\t9296\t4202\tup\thealthy\t-\t0",
        ]

        with patch.object(Util, "get_servers", return_value=[]), patch(
                "org.metadatacenter.worker.ServerWorker.console",
                Console(file=output, width=220, color_system=None)):
            ServerWorker.status(status)

        rendered = output.getvalue()
        self.assertIn("stale binaries: resource; restart them", rendered)
        self.assertIn(
            "ui-main serves an Embeddable Editor other than the one its lock names; run "
            "(cd $CEDAR_HOME/cedar-template-editor && npm ci && npx gulp copy:cee)", rendered)
        self.assertIn("(cd $CEDAR_HOME/cedar-workspace && npm ci && npx gulp copy:cee)", rendered)
        self.assertNotIn("ui-main, ui-workspace; restart", rendered)
        self.assertNotIn("ui-designer serves", rendered)

    def test_machine_status_schema_is_checked(self):
        with self.assertRaisesRegex(ValueError, "unexpected schema"):
            ServerWorker.parse_native_status(["SERVICE PID PORT"])


if __name__ == "__main__":
    unittest.main()


class SourceCurrencyWarningTest(unittest.TestCase):
    """`current` answers a narrower question than an operator reads into it."""

    STATUS = [
        "service\tpid\tport\tlistener\thealth\tbinary\tlog_errors",
        "resource\t101\t9007\tup\thealthy\tcurrent\t0",
        "artifact\t102\t9001\tup\thealthy\tcurrent\t0",
        "ui-main\t-\t4200\tdown\tdown\t-\t0",
    ]

    def _summary(self, findings):
        output = StringIO()
        with (
            patch.object(Util, "get_servers", return_value=[]),
            patch("org.metadatacenter.smoke_gate.source_currency_findings",
                  return_value=findings),
            patch.object(Util, "cedar_home", "/tmp/CEDAR"),
            patch("org.metadatacenter.worker.ServerWorker.console",
                  Console(file=output, width=200, color_system=None)),
        ):
            ServerWorker.status(list(self.STATUS))
        return output.getvalue()

    def test_a_jar_behind_its_head_is_named_even_though_the_column_reads_current(self):
        summary = self._summary([
            "resource was built before its source: its jar predates cedar-resource-server "
            "develop abcdef12",
        ])

        self.assertIn("built before their source: resource", summary)
        self.assertIn("rebuild and restart", summary)

    def test_current_binaries_leave_the_summary_quiet(self):
        self.assertNotIn("built before their source", self._summary([]))

    def test_only_microservices_are_asked(self):
        """A frontend has no jar, and its BINARY column already answers a different question."""
        captured = {}

        def record(_home, services, **_kwargs):
            captured["services"] = list(services)
            return []

        output = StringIO()
        with (
            patch.object(Util, "get_servers", return_value=[]),
            patch("org.metadatacenter.smoke_gate.source_currency_findings",
                  side_effect=record),
            patch.object(Util, "cedar_home", "/tmp/CEDAR"),
            patch("org.metadatacenter.worker.ServerWorker.console",
                  Console(file=output, width=200, color_system=None)),
        ):
            ServerWorker.status(list(self.STATUS))

        self.assertEqual(["resource", "artifact"], captured["services"])


class InfrastructureProbeBoundsTest(unittest.TestCase):
    """A probe that never returns makes the table its own source of delay."""

    class _Server:
        def __init__(self, name, port, admin_port, tag, check_running):
            self.name = name
            self.port = port
            self.admin_port = admin_port
            self.tag = tag
            self.check_running = check_running

    def _servers(self, tag, check_running, count=3):
        return [self._Server(f"server-{n}", 9000 + n, 9100 + n, tag, check_running)
                for n in range(count)]

    def test_a_health_check_probe_is_bounded(self):
        from org.metadatacenter.model.CheckRunning import CheckRunning
        servers = self._servers("infra", CheckRunning.HEALTH_CHECK, count=1)
        status = {}
        with patch.object(Util, "get_servers", return_value=servers), \
                patch.object(ServerWorker, "is_port_open", return_value=True), \
                patch("org.metadatacenter.worker.ServerWorker.requests.head") as head:
            ServerWorker.check_status_of("infra", status)

        self.assertEqual(ServerWorker.PROBE_TIMEOUT_SECONDS, head.call_args.kwargs.get("timeout"))
        self.assertEqual(1, len(status))

    def test_a_response_probe_is_bounded(self):
        from org.metadatacenter.model.CheckRunning import CheckRunning
        servers = self._servers("infra", CheckRunning.RESPONSE, count=1)
        status = {}
        with patch.object(Util, "get_servers", return_value=servers), \
                patch.object(ServerWorker, "is_port_open", return_value=True), \
                patch("org.metadatacenter.worker.ServerWorker.requests.head") as head:
            ServerWorker.check_status_of("infra", status)

        self.assertEqual(ServerWorker.PROBE_TIMEOUT_SECONDS, head.call_args.kwargs.get("timeout"))

    def test_one_slow_server_does_not_hold_up_the_others(self):
        """Serial probing costs their sum; the table is worth the slowest of them."""
        from org.metadatacenter.model.CheckRunning import CheckRunning
        import time
        servers = self._servers("infra", CheckRunning.OPEN_PORT, count=3)
        status = {}

        def slow(host, port):
            time.sleep(0.4)
            return True

        started = time.monotonic()
        with patch.object(Util, "get_servers", return_value=servers), \
                patch.object(ServerWorker, "is_port_open", side_effect=slow):
            ServerWorker.check_status_of("infra", status)
        elapsed = time.monotonic() - started

        self.assertEqual(3, len(status))
        self.assertLess(elapsed, 1.0, "three 0.4s probes ran in sequence")

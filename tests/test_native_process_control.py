import http.server
import importlib.util
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import typer

from typer.testing import CliRunner

from org.metadatacenter import (native, start, start_frontend, start_microservice, stop,
                                stop_frontend)
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.ModeManager import ModeManager
from org.metadatacenter.worker.NativeWorker import NativeWorker
from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker
from org.metadatacenter.worker.StartInfrastructureWorker import StartInfrastructureWorker
from org.metadatacenter.worker.StartMicroserviceWorker import StartMicroserviceWorker
from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker
from org.metadatacenter.worker.StopInfrastructureWorker import StopInfrastructureWorker
from org.metadatacenter.worker.StopMicroserviceWorker import StopMicroserviceWorker

NATIVE_CONTROLLER = (
    Path(__file__).resolve().parents[2]
    / "cedar-development" / "ops" / "cedar-services.sh"
)
FRONTEND_READY = NATIVE_CONTROLLER.with_name("frontend_ready.py")


@patch.dict("os.environ", {"CEDAR_HOME": "/tmp/CEDAR"})
class NativeProcessControlTest(unittest.TestCase):

    def setUp(self):
        self.runtime_patch = patch.object(
            ModeManager, "require_runtime_compatible", side_effect=lambda mode: mode)
        self.runtime_patch.start()

    def tearDown(self):
        self.runtime_patch.stop()

    @patch.object(ModeManager, "current", return_value=CedarMode.NATIVE)
    def test_native_namespace_exposes_start_and_stop(self, _mode):
        runner = CliRunner()

        for action in ("start", "stop"):
            result = runner.invoke(native.app, [action, "--help"])
            self.assertEqual(0, result.exit_code, result.output)

    def test_start_and_stop_are_not_top_level_commands(self):
        import cedar

        runner = CliRunner()
        for action in ("start", "stop"):
            result = runner.invoke(cedar.app, [action, "--help"])
            self.assertEqual(2, result.exit_code, result.output)

    def test_publish_replaces_deploy_as_top_level_command(self):
        import cedar

        runner = CliRunner()
        publish_result = runner.invoke(cedar.app, ["publish", "--help"])
        deploy_result = runner.invoke(cedar.app, ["deploy", "--help"])

        self.assertEqual(0, publish_result.exit_code, publish_result.output)
        self.assertEqual(2, deploy_result.exit_code, deploy_result.output)

    def test_legacy_java_and_uis_aliases_are_not_exposed(self):
        runner = CliRunner()

        for command_group in (start.app, stop.app):
            for alias in ("java", "uis"):
                result = runner.invoke(command_group, [alias, "--help"])
                self.assertEqual(2, result.exit_code)

    @patch.object(NativeWorker, "infrastructure_gaps", return_value=None)
    @patch.object(NativeWorker, "start")
    @patch.object(StartInfrastructureWorker, "all")
    def test_aggregate_start_uses_one_application_controller_invocation(
            self, infrastructure, applications, _gaps):
        start.all_all()

        infrastructure.assert_called_once_with()
        applications.assert_called_once_with()

    def test_start_waits_for_infrastructure_before_the_applications(self):
        """A microservice reaches Neo4j, Mongo and Keycloak while it boots, so it must be there."""
        order = []
        gaps = [["Keycloak"], ["Keycloak"], []]
        with patch.object(NativeWorker, "infrastructure_gaps",
                          side_effect=[["Keycloak"]] + gaps), \
                patch.object(StartInfrastructureWorker, "all",
                             side_effect=lambda: order.append("infra")), \
                patch.object(NativeWorker, "start", side_effect=lambda: order.append("applications")), \
                patch("org.metadatacenter.start.time.sleep"):
            start.all_all()

        self.assertEqual(["infra", "applications"], order)

    def test_start_stops_when_infrastructure_never_arrives(self):
        with patch.object(NativeWorker, "infrastructure_gaps", return_value=["Keycloak"]), \
                patch.object(StartInfrastructureWorker, "all"), \
                patch.object(NativeWorker, "start") as applications, \
                patch("org.metadatacenter.start.time.sleep"), \
                patch("org.metadatacenter.start.time.monotonic", side_effect=[0, 1000, 2000]):
            with self.assertRaises(typer.Exit):
                start.all_all()

        applications.assert_not_called()

    @patch.object(StopInfrastructureWorker, "all")
    @patch.object(NativeWorker, "stop")
    def test_aggregate_stop_uses_one_application_controller_invocation(self, applications, infrastructure):
        stop.all_all()

        applications.assert_called_once_with()
        infrastructure.assert_called_once_with()

    def test_backend_start_uses_dependency_order(self):
        order = []
        with patch.object(NativeWorker, "infrastructure_gaps", return_value=None), \
                patch.object(StartInfrastructureWorker, "all", side_effect=lambda: order.append("infra")), \
                patch.object(StartMicroserviceWorker, "all", side_effect=lambda: order.append("microservices")):
            start.backend_all()

        self.assertEqual(["infra", "microservices"], order)

    def test_backend_stop_reverses_dependency_order(self):
        order = []
        with patch.object(StopMicroserviceWorker, "all", side_effect=lambda: order.append("microservices")), \
                patch.object(StopInfrastructureWorker, "all", side_effect=lambda: order.append("infra")):
            stop.backend_all()

        self.assertEqual(["microservices", "infra"], order)

    @patch("org.metadatacenter.worker.NativeWorker.Worker.execute_generic_shell_commands")
    def test_all_microservices_use_one_headless_controller_invocation(self, execute):
        StartMicroserviceWorker.all()

        command = execute.call_args.args[0][0]
        self.assertIn("cedar-services.sh start", command)
        self.assertTrue(all(name in command for name in NativeWorker.MICROSERVICES))
        self.assertNotIn("osascript", command)

    @patch("org.metadatacenter.worker.NativeWorker.Worker.execute_generic_shell_commands")
    def test_all_seven_frontends_use_one_headless_controller_invocation(self, execute):
        StartFrontendWorker.all()

        command = execute.call_args.args[0][0]
        self.assertIn("cedar-services.sh start", command)
        self.assertTrue(all(name in command for name in NativeWorker.FRONTENDS))
        self.assertNotIn("osascript", command)

    @patch("org.metadatacenter.worker.NativeWorker.Worker.execute_generic_shell_commands")
    def test_stop_groups_use_the_same_controller(self, execute):
        StopMicroserviceWorker.all()
        StopFrontendWorker.all()

        commands = [call.args[0][0] for call in execute.call_args_list]
        self.assertTrue(all("cedar-services.sh stop" in command for command in commands))
        self.assertTrue(all("osascript" not in command for command in commands))

    @patch.object(NativeWorker, "start")
    def test_native_start_propagates_controller_failure(self, start_native):
        start_native.return_value = type("Result", (), {"returncode": 23})()
        with patch.object(ModeManager, "current", return_value=CedarMode.NATIVE):
            result = CliRunner().invoke(start.app, ["microservices"])

        self.assertEqual(23, result.exit_code, result.output)

    @patch.object(NativeWorker, "stop")
    def test_native_stop_propagates_controller_failure(self, stop_native):
        stop_native.return_value = type("Result", (), {"returncode": 24})()
        with patch.object(ModeManager, "current", return_value=CedarMode.NATIVE):
            result = CliRunner().invoke(stop.app, ["microservices"])

        self.assertEqual(24, result.exit_code, result.output)

    @patch("org.metadatacenter.worker.StartInfrastructureWorker.Worker.execute_generic_shell_commands")
    def test_infrastructure_start_is_headless(self, execute):
        StartInfrastructureWorker.all()

        command = execute.call_args.args[0][0]
        self.assertIn("start-infrastructure-all.sh", command)
        self.assertNotIn("osascript", command)

    @patch("org.metadatacenter.worker.StopInfrastructureWorker.Worker.execute_generic_shell_commands")
    def test_infrastructure_stop_is_headless(self, execute):
        StopInfrastructureWorker.all()

        command = execute.call_args.args[0][0]
        self.assertIn("stop-infrastructure-all.sh", command)
        self.assertNotIn("osascript", command)

    @patch("org.metadatacenter.worker.NativeWorker.Worker.execute_generic_shell_commands")
    def test_the_openview_command_is_named_for_its_service(self, execute):
        StartMicroserviceWorker.openview()

        self.assertIn("cedar-services.sh start openview", execute.call_args.args[0][0])

    def test_the_superseded_open_spelling_still_starts_openview(self):
        """It was the published name, so a script that learned it keeps working."""
        command = start_microservice.app.registered_commands
        names = {registered.name for registered in command}

        self.assertIn("openview", names)
        self.assertIn("open", names)
        self.assertTrue(next(r for r in command if r.name == "open").hidden)
        self.assertFalse(next(r for r in command if r.name == "openview").hidden)

    @patch.object(ModeManager, "require_surface", return_value=CedarMode.NATIVE)
    @patch.object(NativeWorker, "restart")
    def test_restart_refuses_a_name_no_application_answers_to(self, restart, _surface):
        """Reject an invalid name before the native controller runs."""
        runner = CliRunner()

        result = runner.invoke(native.app, ["restart", "monitor", "unknown-service"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("unknown-service", result.output)
        restart.assert_not_called()

    @patch.object(ModeManager, "require_surface", return_value=CedarMode.NATIVE)
    @patch.object(NativeWorker, "restart")
    def test_restart_passes_applications_it_recognizes_through(self, restart, _surface):
        runner = CliRunner()
        restart.return_value.returncode = 0

        result = runner.invoke(native.app, ["restart", "monitor", "ui-main"])

        self.assertEqual(0, result.exit_code, result.output)
        restart.assert_called_once_with(["monitor", "ui-main"])

    @patch("org.metadatacenter.worker.NativeWorker.ServerWorker.status")
    @patch("org.metadatacenter.worker.NativeWorker.Worker.execute_generic_shell_commands")
    def test_native_status_includes_process_and_host_port_checks(self, execute, host_status):
        execute.return_value.returncode = 0
        result = NativeWorker.status()

        command = execute.call_args.args[0][0]
        self.assertIn("cedar-services.sh status-tsv", command)
        self.assertFalse(execute.call_args.kwargs["show_command"])
        self.assertFalse(execute.call_args.kwargs["echo_streams"])
        host_status.assert_called_once_with(execute.return_value)
        self.assertIs(result, execute.return_value)

    @unittest.skipUnless(
        NATIVE_CONTROLLER.is_file(),
        "requires the sibling cedar-development checkout",
    )
    def test_infrastructure_inventory_excludes_docker_port_forwarders(self):
        script = f'''\
export CEDAR_SERVICES_LIBRARY_ONLY=true
export CEDAR_SERVICES_INSPECT_ONLY=true
source "{NATIVE_CONTROLLER}"
port_owners() {{
  case "$1" in
    80) echo 101 ;;
    443) echo 202 ;;
  esac
}}
process_command() {{
  case "$1" in
    101) echo '/opt/homebrew/opt/nginx/bin/nginx' ;;
    202) echo '/Applications/Docker.app/Contents/MacOS/com.docker.backend' ;;
  esac
}}
port_open() {{ return 1; }}
running_infrastructure
'''
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("nginx-http (port 80, pid 101)\n", result.stdout)

    @unittest.skipUnless(
        NATIVE_CONTROLLER.is_file(),
        "requires the sibling cedar-development checkout",
    )
    def test_native_inventory_checks_every_listener_on_a_service_port(self):
        script = f'''\
export CEDAR_SERVICES_LIBRARY_ONLY=true
export CEDAR_SERVICES_INSPECT_ONLY=true
source "{NATIVE_CONTROLLER}"
pidfile() {{ echo /does/not/exist; }}
app_port() {{ echo 9009; }}
names() {{ echo group; }}
port_owners() {{ printf '101\\n202\\n'; }}
is_service_process() {{ [ "$2" = 202 ]; }}
running
'''
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("group\n", result.stdout)


class _Routes(http.server.BaseHTTPRequestHandler):
    """Answers each path from its server's table with a status, a content type and a body.

    A route may be a callable, which runs on the request's own thread and may block it, as a
    development server blocks a request while it compiles or optimizes.
    """

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        self.server.requested.append(path)
        route = self.server.routes.get(path)
        status, content_type, body = (route() if callable(route) else route) or (
            404, "text/plain", b"not found")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _page(*tags):
    return 200, "text/html; charset=UTF-8", (
        "<!doctype html><html><head>" + "".join(tags) + "</head><body></body></html>").encode()


def _script(text="void 0;"):
    return 200, "text/javascript", text.encode()


@unittest.skipUnless(
    FRONTEND_READY.is_file(),
    "requires a sibling cedar-development checkout whose controller checks frontend pages",
)
class FrontendReadinessTest(unittest.TestCase):
    """What `start` and `restart` wait for before they call a frontend ready.

    A development server opens its port before it serves the page behind it, so an answer from the
    root says too little: the reactor's smoke once timed out loading a page the controller had just
    reported ready. Readiness is the page and everything a browser loads with it. These run the
    controller's own wait against servers on loopback, which stand in for the frontends.
    """

    # A request still waiting when its pass ends. Which bound ended it depends on where in the
    # budget the last pass fell, so either wording is the same finding.
    STILL_LOADING = r"(did not finish within 1s|was still loading when the readiness budget ran out)"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.log = self.home / "frontend.log"
        self.log.write_text("Application bundle generation complete.\n")

    def serve(self, routes):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Routes)
        server.routes, server.requested = routes, []
        # A request the check gave up on is answered into a closed socket, which is expected here,
        # and closing the server must not wait for a request still being held.
        server.handle_error = lambda request, address: None
        server.block_on_close = False
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def run_controller(self, body, budget):
        script = f'''\
export CEDAR_SERVICES_LIBRARY_ONLY=true
export CEDAR_SERVICES_INSPECT_ONLY=true
source "{NATIVE_CONTROLLER}"
logfile() {{ echo "{self.log}"; }}
remove_launchd_job() {{ echo "removed $1"; }}
{body}
'''
        environment = dict(os.environ, CEDAR_HOME=str(self.home),
                           CEDAR_START_READY_TIMEOUT=str(budget),
                           CEDAR_FRONTEND_READY_REQUEST_TIMEOUT="1",
                           CEDAR_HEALTH_PROBE_TIMEOUT="1")
        for name in ("CEDAR_HOST", "CEDAR_CA_HOME", "CEDAR_WORKSPACE_FRONTEND_URL",
                     "CEDAR_TEMPLATE_DESIGNER_FRONTEND_URL", "CEDAR_FRONTEND_READY_ORIGIN"):
            environment.pop(name, None)
        started = time.monotonic()
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                env=environment, timeout=budget + 60, check=False)
        return result, time.monotonic() - started

    def await_ready(self, port, name="ui-openview", budget=2):
        """Runs the wait for one frontend served on `port`, after the root probe status uses."""
        return self.run_controller(f'''\
app_port() {{ echo {port}; }}
fe_origin() {{ echo "http://127.0.0.1:{port}"; }}
echo "root: $(health_of {name})"
await_ready {name}
''', budget)

    def test_a_port_that_accepts_connections_and_answers_nothing_is_not_ready(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)  # The kernel completes each connection; nothing ever answers one.
        port = listener.getsockname()[1]

        result, _ = self.await_ready(port)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("ready ui-openview", result.stdout)
        self.assertRegex(result.stderr, rf"http://127\.0\.0\.1:{port}/ {self.STILL_LOADING}")
        self.assertIn("removed ui-openview", result.stdout)

    def test_a_page_served_before_its_dependencies_are_optimized_is_not_ready(self):
        held = threading.Event()
        self.addCleanup(held.set)
        server = self.serve({
            "/": _page('<script type="module" src="/main.js"></script>'),
            "/main.js": _script('import { core } from "/deps/core.js";\ncore();\n'),
            "/deps/core.js": lambda: held.wait(30) and _script("export const core = () => 0;"),
        })

        result, _ = self.await_ready(server.server_port)

        # The root answers at once, which is all the old wait asked for.
        self.assertIn("root: healthy", result.stdout)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("ready ui-openview", result.stdout)
        self.assertRegex(result.stderr, rf"/deps/core\.js {self.STILL_LOADING}")
        # It answers its port, so it is left running for inspection rather than removed.
        self.assertIn("left running", result.stderr)
        self.assertNotIn("removed", result.stdout)

    def test_a_frontend_is_ready_once_its_optimized_dependencies_load(self):
        # The dependency is held for 1.5 s from the first request for it, as the optimizer holds it from
        # the first page load, so the wait is measured from a moment the readiness check has reached.
        optimized = threading.Event()
        asked = threading.Lock()
        timers = []
        self.addCleanup(lambda: [timer.cancel() for timer in timers])

        def held():
            with asked:
                if not timers:
                    timers.append(threading.Timer(1.5, optimized.set))
                    timers[0].start()
            return optimized.wait(30) and _script("export const core = () => 0;")

        server = self.serve({
            "/": _page('<script type="module" src="/main.js"></script>'),
            "/main.js": _script('import { core } from "/deps/core.js";\ncore();\n'),
            "/deps/core.js": held,
        })

        result, elapsed = self.await_ready(server.server_port, budget=10)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("ready ui-openview", result.stdout)
        self.assertGreaterEqual(elapsed, 1.5)
        self.assertIn("/deps/core.js", server.requested)

    def test_an_index_whose_main_script_fails_is_not_ready(self):
        failures = {
            "answered 404": (404, "text/plain", b"not found"),
            "answered 500": (500, "text/plain", b"compilation failed"),
            # Gulp answers a missing file with the index, so a page in place of a script is missing.
            "answered with a page in place of a script": _page(),
        }
        for reason, answer in failures.items():
            with self.subTest(reason):
                server = self.serve({
                    "/": _page('<script src="/main.js"></script>'),
                    "/main.js": answer,
                })

                result, _ = self.await_ready(server.server_port, budget=1)

                self.assertIn("root: healthy", result.stdout)
                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertNotIn("ready ui-openview", result.stdout)
                self.assertIn(f"/main.js {reason}", result.stderr)
                self.assertIn("left running", result.stderr)

    def test_a_frontend_that_never_serves_fails_clearly_within_the_budget(self):
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()

        result, elapsed = self.await_ready(port, budget=3)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn(
            f"ui-openview: never served its page within 3s (down): "
            f"http://127.0.0.1:{port}/ refused the connection", result.stderr)
        self.assertIn("Application bundle generation complete.", result.stderr)
        self.assertIn("removed ui-openview", result.stdout)
        # The budget, then one bounded probe of the root for the report and a second of polling.
        self.assertLess(elapsed, 3 + 1 + 3)

    def test_a_frontend_serving_its_page_and_what_the_page_loads_is_ready(self):
        server = self.serve({
            "/": _page(
                '<base href="/build/">',
                '<link rel="icon" href="favicon.ico">',
                '<link rel="stylesheet" href="styles.css">',
                '<script src="http://127.0.0.1:1/elsewhere.js"></script>',
                '<script src="polyfills.js"></script>',
                '<link rel="modulepreload" href="chunk.js">',
                '<script type="module" src="main.js"></script>',
            ),
            "/build/styles.css": (200, "text/css", b"body {}"),
            "/build/polyfills.js": _script(),
            "/build/chunk.js": _script("export const a = 1;"),
            "/build/main.js": _script('import{a as b}from"./chunk.js";import "./side.js";b();'),
            "/build/side.js": _script(),
        })

        result, _ = self.await_ready(server.server_port)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("ready ui-openview", result.stdout)
        self.assertEqual(
            {"/", "/build/styles.css", "/build/polyfills.js", "/build/chunk.js", "/build/main.js",
             "/build/side.js"},
            set(server.requested))

    def test_the_workspace_is_ready_only_when_the_build_its_bootstrap_loads_is(self):
        bootstrap = _page("<script>fetch('/workspace-build/index.html')</script>")
        build = _page('<script src="/workspace-build/main.js" type="module"></script>')
        cases = {
            "missing build": ({"/": bootstrap}, 1),
            "served build": ({"/": bootstrap, "/workspace-build/index.html": build,
                              "/workspace-build/main.js": _script()}, 0),
        }
        for case, (routes, expected) in cases.items():
            with self.subTest(case):
                server = self.serve(routes)

                result, _ = self.await_ready(server.server_port, name="ui-workspace")

                self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
                if expected:
                    self.assertIn("/workspace-build/index.html answered 404", result.stderr)
                else:
                    self.assertIn("ready ui-workspace", result.stdout)

    def test_a_frontend_is_asked_on_its_own_port_unless_nginx_is_asked_for(self):
        names = ["ui-main", "ui-workspace", "ui-designer", "ui-openview", "ui-content",
                 "ui-monitoring", "ui-bridging"]
        listing = "; ".join(f'echo "{name} $(fe_origin {name})"' for name in names)

        # The hybrid topology starts the frontends before the Docker nginx in front of them.
        result, _ = self.run_controller(f'CEDAR_HOST=example.test; {listing}', budget=1)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["ui-main http://127.0.0.1:4200", "ui-workspace http://127.0.0.1:4201",
             "ui-designer http://127.0.0.1:4202", "ui-openview http://127.0.0.1:4220",
             "ui-content http://127.0.0.1:4240", "ui-monitoring http://127.0.0.1:4300",
             "ui-bridging http://127.0.0.1:4340"],
            result.stdout.splitlines())

        result, _ = self.run_controller(
            f'CEDAR_HOST=example.test; CEDAR_FRONTEND_READY_ORIGIN=public; {listing}', budget=1)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([
            "ui-main https://cedar.example.test",
            "ui-workspace https://workspace.example.test",
            "ui-designer https://designer.example.test",
            "ui-openview https://openview.example.test",
            # Native nginx serves content.<host> from the checkout, so only the port reaches it.
            "ui-content http://127.0.0.1:4240",
            "ui-monitoring https://monitoring.example.test",
            "ui-bridging https://bridging.example.test",
        ], result.stdout.splitlines())

    def test_the_local_ca_is_trusted_without_the_strict_rfc_5280_profile(self):
        # Python 3.13 turned the strict profile on by default, and it refuses the CEDAR CA for
        # lacking a key usage extension that browsers, curl and Node do not require.
        spec = importlib.util.spec_from_file_location("frontend_ready", FRONTEND_READY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        context = module.tls_context(None)

        self.assertFalse(context.verify_flags & ssl.VERIFY_X509_STRICT)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)


if __name__ == "__main__":
    unittest.main()


class FrontendNamingTest(unittest.TestCase):
    """The frontend subcommands name a frontend bare; the native controller names it ui-<name>.

    Nothing translates between the two any more, so the two vocabularies have to stay in step by
    construction. These assertions are what the old TARGETS dictionary used to be: they fail when a
    frontend is added to one side and forgotten on the other, and when a name loses the ui- prefix
    that keeps it apart from the like-named microservice.
    """

    def command_names(self, app):
        aggregates = ("all", "split-frontends")
        return sorted(command.name for command in app.registered_commands
                      if command.name not in aggregates)

    def test_every_frontend_subcommand_names_a_native_frontend(self):
        for app in (start_frontend.app, stop_frontend.app):
            with self.subTest(app=app.info.name or "frontend"):
                for name in self.command_names(app):
                    self.assertIn("ui-" + name, NativeWorker.FRONTENDS)

    def test_start_and_stop_expose_the_same_frontends(self):
        names = self.command_names(start_frontend.app)
        self.assertEqual(len(NativeWorker.FRONTENDS), len(names))
        self.assertEqual(names, self.command_names(stop_frontend.app))

    def test_every_native_frontend_is_prefixed_and_distinct_from_a_microservice(self):
        for name in NativeWorker.FRONTENDS:
            self.assertTrue(name.startswith("ui-"), name)
            self.assertNotIn(name, NativeWorker.MICROSERVICES)


class InfrastructureAlreadyRunningTest(unittest.TestCase):
    """Starting infrastructure that already runs fails; asking first is the difference."""

    CONTROLLER = (
        'INFRASTRUCTURE_PORTS=(\n'
        '  "nginx-http 80"\n'
        '  "mongodb 27017"\n'
        '  # a comment the list carries\n'
        '  "keycloak 8080"\n'
        ')\n'
    )

    def _ports(self, tmp):
        path = Path(tmp) / "cedar-services.sh"
        path.write_text(self.CONTROLLER, encoding="utf-8")
        return path

    def test_the_managed_ports_come_from_the_controller(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(NativeWorker, "controller_path", return_value=str(self._ports(tmp))):
                self.assertEqual(
                    {"nginx-http": "80", "mongodb": "27017", "keycloak": "8080"},
                    NativeWorker.infrastructure_ports())

    def _gaps(self, listening, returncode=0):
        result = unittest.mock.MagicMock()
        result.returncode = returncode
        result.__iter__ = lambda _self: iter(listening)
        with patch.object(
            NativeWorker, "infrastructure_ports",
            return_value={"nginx-http": "80", "mongodb": "27017", "keycloak": "8080"},
        ), patch.object(NativeWorker, "execute", return_value=result):
            return NativeWorker.infrastructure_gaps()

    def test_every_port_listening_is_no_gap_at_all(self):
        self.assertEqual([], self._gaps([
            "nginx-http (port 80, pid 1)",
            "mongodb (port 27017, pid 2)",
            "keycloak (port 8080, pid 3)",
        ]))

    def test_a_silent_port_is_named(self):
        self.assertEqual(["keycloak"], self._gaps([
            "nginx-http (port 80, pid 1)",
            "mongodb (port 27017, pid 2)",
        ]))

    def test_an_unreadable_controller_answers_nothing(self):
        """None means 'cannot say', which must start infrastructure rather than skip it."""
        self.assertIsNone(self._gaps([], returncode=1))

    def test_start_all_skips_infrastructure_that_is_already_listening(self):
        with (
            patch.object(NativeWorker, "infrastructure_gaps", return_value=[]),
            patch.object(StartInfrastructureWorker, "all") as infrastructure,
            patch.object(NativeWorker, "start") as applications,
        ):
            start.all_all()

        infrastructure.assert_not_called()
        applications.assert_called_once_with()

    def test_start_all_starts_infrastructure_when_a_port_is_silent(self):
        # The gap is what makes it start, and it closes once the port is served, which is what
        # releases the wait before the applications go.
        with (
            patch.object(NativeWorker, "infrastructure_gaps", side_effect=[["keycloak"], []]),
            patch.object(StartInfrastructureWorker, "all") as infrastructure,
            patch.object(NativeWorker, "start") as applications,
        ):
            start.all_all()

        infrastructure.assert_called_once_with()
        applications.assert_called_once_with()

    def test_start_all_starts_infrastructure_when_the_controller_cannot_say(self):
        with (
            patch.object(NativeWorker, "infrastructure_gaps", return_value=None),
            patch.object(StartInfrastructureWorker, "all") as infrastructure,
            patch.object(NativeWorker, "start"),
        ):
            start.all_all()

        infrastructure.assert_called_once_with()



class RefreshStaleDependenciesTest(unittest.TestCase):
    """The start that meets a moved lockfile can install past it when asked to.

    The condition is exact and its repair is the same command every time, so refusing seven
    starts for seven identical reasons after a release — which rewrites every frontend lockfile
    at once — leaves the operator running that command seven times. It stays opt-in because
    `npm ci` reinstalls from scratch, and a start is not where somebody expects to wait.
    """

    def setUp(self):
        NativeWorker.refresh_stale_dependencies = False
        self.addCleanup(setattr, NativeWorker, "refresh_stale_dependencies", False)
        self.runner = CliRunner()

    def _command(self, invoke):
        with patch.object(NativeWorker, "controller_path", return_value="/controller.sh"), \
             patch("org.metadatacenter.worker.Worker.Worker.execute_generic_shell_commands",
                   return_value=subprocess.CompletedProcess([], 0)) as execute, \
             patch.object(ModeManager, "require_surface", return_value=CedarMode.NATIVE):
            invoke()
        return execute.call_args[0][0][0]

    def test_the_controller_is_not_asked_to_install_by_default(self):
        command = self._command(lambda: NativeWorker.start(("ui-content",)))

        self.assertNotIn("CEDAR_REFRESH_STALE_DEPENDENCIES", command)

    def test_the_worker_flag_reaches_the_controller(self):
        NativeWorker.refresh_stale_dependencies = True

        command = self._command(lambda: NativeWorker.start(("ui-content",)))

        self.assertTrue(command.startswith("CEDAR_REFRESH_STALE_DEPENDENCIES=1 "), command)

    def test_the_decision_travels_in_the_script_rather_than_the_environment(self):
        """A subprocess inherits the invocation's resolved profile, not os.environ.

        That snapshot is taken before any command runs, so a callback setting os.environ would
        never reach the controller. The prefix is in the script text for that reason.
        """
        command = self._command(
            lambda: NativeWorker.restart(("ui-content",), refresh_dependencies=True))

        self.assertIn("CEDAR_REFRESH_STALE_DEPENDENCIES=1", command)

    def test_the_start_option_sets_the_worker_flag(self):
        with patch.object(ModeManager, "require_surface", return_value=CedarMode.NATIVE), \
             patch.object(StartFrontendWorker, "all",
                          return_value=subprocess.CompletedProcess([], 0)):
            result = self.runner.invoke(start.app, ["--refresh-dependencies", "frontends"])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertTrue(NativeWorker.refresh_stale_dependencies)

    def test_the_controller_offers_the_option_in_its_refusal(self):
        self.assertIn("--refresh-dependencies", NATIVE_CONTROLLER.read_text(encoding="utf-8"))


class ReactorReadinessOriginTest(unittest.TestCase):
    """The reactor's restart waits for each page through nginx; any other start asks the port.

    The reactor's smoke follows the restart and loads every page through nginx, which is already
    up. A plain start cannot assume that: the hybrid topology starts the frontends before the
    Docker nginx in front of them.
    """

    def _command(self, invoke):
        with patch.object(NativeWorker, "controller_path", return_value="/controller.sh"), \
             patch("org.metadatacenter.worker.Worker.Worker.execute_generic_shell_commands",
                   return_value=subprocess.CompletedProcess([], 0)) as execute:
            invoke()
        return execute.call_args[0][0][0]

    def test_only_a_restart_that_asks_waits_through_nginx(self):
        self.assertNotIn("CEDAR_FRONTEND_READY_ORIGIN",
                         self._command(lambda: NativeWorker.restart(("ui-main",))))
        self.assertNotIn("CEDAR_FRONTEND_READY_ORIGIN",
                         self._command(lambda: NativeWorker.start(("ui-main",))))
        self.assertTrue(
            self._command(lambda: NativeWorker.restart(("ui-main",), ready_through_nginx=True))
            .startswith("CEDAR_FRONTEND_READY_ORIGIN=public /controller.sh restart ui-main"))

    def test_the_reactor_refreshes_live_dependencies_and_waits_through_nginx(self):
        from org.metadatacenter import build, cee_demos
        from org.metadatacenter.frontend_inventory import inventory
        from org.metadatacenter.util.GlobalContext import GlobalContext
        from org.metadatacenter.util.InvocationContext import InvocationContext, use_context

        selection = unittest.mock.Mock(evidence="evidence")
        with use_context(InvocationContext(environment=dict(os.environ))), \
             patch("org.metadatacenter.frontend_inventory.inventory", return_value=inventory()), \
             patch.object(build, "check_design_tokens", return_value=0), \
             patch.object(build, "execute_build", return_value=selection), \
             patch.object(cee_demos, "check_selection"), \
             patch.object(build.reactor, "activate_runtime"), \
             patch.object(build.reactor, "store_root", return_value=Path("/reactor")), \
             patch.object(NativeWorker, "restart",
                          return_value=subprocess.CompletedProcess([], 3)) as restart:
            GlobalContext.init_task_operators()
            result = CliRunner().invoke(build.app, ["frontends"])

        self.assertEqual(3, result.exit_code, result.output)
        restart.assert_called_once_with(
            NativeWorker.FRONTENDS, refresh_dependencies=True, ready_through_nginx=True)

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import native, start, start_frontend, stop, stop_frontend
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

    @patch.object(NativeWorker, "start")
    @patch.object(StartInfrastructureWorker, "all")
    def test_aggregate_start_uses_one_application_controller_invocation(self, infrastructure, applications):
        start.all_all()

        infrastructure.assert_called_once_with()
        applications.assert_called_once_with()

    @patch.object(StopInfrastructureWorker, "all")
    @patch.object(NativeWorker, "stop")
    def test_aggregate_stop_uses_one_application_controller_invocation(self, applications, infrastructure):
        stop.all_all()

        applications.assert_called_once_with()
        infrastructure.assert_called_once_with()

    def test_backend_start_uses_dependency_order(self):
        order = []
        with patch.object(StartInfrastructureWorker, "all", side_effect=lambda: order.append("infra")), \
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
    def test_open_cli_name_maps_to_openview_service(self, execute):
        StartMicroserviceWorker.open()

        self.assertIn("cedar-services.sh start openview", execute.call_args.args[0][0])

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

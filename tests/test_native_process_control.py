import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import native, start, stop
from org.metadatacenter.worker.NativeWorker import NativeWorker
from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker
from org.metadatacenter.worker.StartInfrastructureWorker import StartInfrastructureWorker
from org.metadatacenter.worker.StartMicroserviceWorker import StartMicroserviceWorker
from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker
from org.metadatacenter.worker.StopInfrastructureWorker import StopInfrastructureWorker
from org.metadatacenter.worker.StopMicroserviceWorker import StopMicroserviceWorker


@patch.dict("os.environ", {"CEDAR_HOME": "/tmp/CEDAR"})
class NativeProcessControlTest(unittest.TestCase):

    def test_native_namespace_exposes_start_and_stop(self):
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
        result = NativeWorker.status()

        command = execute.call_args.args[0][0]
        self.assertIn("cedar-services.sh status", command)
        host_status.assert_called_once_with()
        self.assertIs(result, execute.return_value)


if __name__ == "__main__":
    unittest.main()

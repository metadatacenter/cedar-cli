import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

import cedar
from org.metadatacenter import docker, native
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.model.CedarProfile import CedarProfile
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.worker.DockerWorker import DockerWorker
from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker


class ModeCommandTest(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()
        self.temporary = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temporary.name) / "mode.json"
        self.state_patch = patch.object(ModeManager, "state_path", return_value=self.state_path)
        self.state_patch.start()
        self.runtime_patch = patch.object(
            ModeManager, "require_runtime_compatible", side_effect=lambda mode: mode)
        self.runtime_patch.start()
        self.docker_start_patch = patch.object(
            ModeManager, "require_docker_start_compatible", side_effect=lambda mode: mode)
        self.docker_start_patch.start()
        self.services_stopped_patch = patch.object(
            ModeManager, "require_selected_services_stopped", side_effect=lambda mode: mode)
        self.services_stopped_patch.start()
        self.original_environment = {
            name: os.environ.pop(name)
            for name in ModeManager.PERSISTED_ENVIRONMENT
            if name in os.environ
        }

    def tearDown(self):
        os.environ.update(self.original_environment)
        self.services_stopped_patch.stop()
        self.docker_start_patch.stop()
        self.runtime_patch.stop()
        self.state_patch.stop()
        self.temporary.cleanup()

    @patch.object(ModeManager, "validate_mode", return_value={})
    def test_mode_is_recorded_once_and_can_be_cleared(self, validate):
        configured = self.runner.invoke(
            cedar.app, ["mode", "native", "--profile", "develop"])
        recorded = json.loads(self.state_path.read_text())
        repeated = self.runner.invoke(cedar.app, ["mode", "hybrid", "--profile", "develop"])
        shown = self.runner.invoke(cedar.app, ["mode"])
        cleared = self.runner.invoke(cedar.app, ["mode", "--clear"])

        self.assertEqual(0, configured.exit_code, configured.output)
        self.assertEqual({"mode": "native", "profile": "develop"}, recorded)
        self.assertEqual(1, repeated.exit_code, repeated.output)
        self.assertIn("already set to native", repeated.output)
        self.assertIn("CEDAR mode: native, profile develop", shown.output)
        self.assertEqual(0, cleared.exit_code, cleared.output)
        self.assertFalse(self.state_path.exists())
        validate.assert_called_once_with(CedarMode.NATIVE, CedarProfile.DEVELOP)

    @patch.object(ModeManager, "validate_mode", return_value={})
    def test_a_native_mode_must_name_its_environment(self, validate):
        result = self.runner.invoke(cedar.app, ["mode", "native"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("name the environment with", result.output)
        self.assertFalse(self.state_path.exists())
        validate.assert_not_called()

    @patch.object(ModeManager, "validate_mode", return_value={})
    def test_a_host_recorded_before_profiles_can_name_its_environment_in_place(self, validate):
        """Adding the missing fact must not require stopping a running host's applications."""
        self.state_path.write_text('{"mode": "native"}\n', encoding="utf-8")

        adopted = self.runner.invoke(cedar.app, ["mode", "native", "--profile", "server"])

        self.assertEqual(0, adopted.exit_code, adopted.output)
        self.assertEqual({"mode": "native", "profile": "server"},
                         json.loads(self.state_path.read_text()))
        validate.assert_called_once_with(CedarMode.NATIVE, CedarProfile.SERVER)

    @patch.object(ModeManager, "validate_mode", return_value={})
    def test_a_recorded_profile_is_not_changed_in_place(self, _validate):
        self.state_path.write_text('{"mode": "native", "profile": "server"}\n', encoding="utf-8")

        result = self.runner.invoke(cedar.app, ["mode", "native", "--profile", "develop"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("already set to native", result.output)
        self.assertEqual({"mode": "native", "profile": "server"},
                         json.loads(self.state_path.read_text()))

    def test_docker_mode_takes_no_native_profile(self):
        result = self.runner.invoke(cedar.app, ["mode", "docker", "--profile", "server"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("runs no native applications", result.output)
        self.assertFalse(self.state_path.exists())

    def test_mode_query_reports_unconfigured_state(self):
        result = self.runner.invoke(cedar.app, ["mode"])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("CEDAR mode is not set", result.output)

    @patch.object(DockerWorker, "_clear_active_deployment")
    @patch.object(
        DockerWorker, "active_deployment", return_value=DockerDeploymentMode.FULL)
    @patch.object(
        DockerWorker, "_docker_server_version", return_value=(None, "daemon unavailable"))
    def test_force_clear_recovers_when_docker_is_deliberately_off(
            self, _daemon, _active, clear_deployment):
        self.state_path.write_text('{"mode": "docker"}\n', encoding="utf-8")

        refused = self.runner.invoke(cedar.app, ["mode", "--clear"])
        cleared = self.runner.invoke(cedar.app, ["mode", "--clear", "--force"])

        self.assertEqual(1, refused.exit_code, refused.output)
        self.assertIn("Docker is unavailable", refused.output)
        self.assertIn("--clear --force", refused.output)
        self.assertEqual(0, cleared.exit_code, cleared.output)
        self.assertIn("deployment record discarded", cleared.output)
        self.assertFalse(self.state_path.exists())
        clear_deployment.assert_called_once_with()

    @patch.object(DockerWorker, "_clear_active_deployment")
    @patch.object(
        DockerWorker, "active_deployment", return_value=DockerDeploymentMode.FULL)
    @patch.object(
        DockerWorker, "running_compose_projects", return_value={"cedar-microservices"})
    @patch.object(
        DockerWorker, "_docker_server_version", return_value=("29.6.2", None))
    def test_force_clear_cannot_bypass_running_containers(
            self, _daemon, _projects, _active, clear_deployment):
        self.state_path.write_text('{"mode": "docker"}\n', encoding="utf-8")

        result = self.runner.invoke(cedar.app, ["mode", "--clear", "--force"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("still running", result.output)
        self.assertIn("cannot bypass", result.output)
        self.assertTrue(self.state_path.exists())
        clear_deployment.assert_not_called()

    @patch.object(
        DockerWorker, "active_deployment", return_value=DockerDeploymentMode.FULL)
    @patch.object(
        DockerWorker, "running_compose_projects", return_value={"cedar-admin"})
    @patch.object(
        DockerWorker, "_docker_server_version", return_value=("29.6.2", None))
    def test_clear_names_the_separate_admin_cleanup_command(
            self, _daemon, _projects, _active):
        self.state_path.write_text('{"mode": "docker"}\n', encoding="utf-8")

        result = self.runner.invoke(cedar.app, ["mode", "--clear"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("cedarcli docker stop", result.output)
        self.assertIn("admin before clearing", result.output)
        self.assertNotIn("cedarcli docker stop all", result.output)

    def test_force_requires_clear(self):
        result = self.runner.invoke(cedar.app, ["mode", "native", "--force"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("valid only with --clear", result.output)

    @patch.dict("os.environ", {
        "CEDAR_IMAGE_PREFIX": "registry.example/cedar",
        "CEDAR_TERMINOLOGY_STORE_CATALOG": "",
    }, clear=False)
    @patch.object(ModeManager, "validate_mode", return_value={"docker": {}})
    def test_docker_configuration_persists_installation_overrides(self, _validate):
        result = self.runner.invoke(cedar.app, ["mode", "docker"])

        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual({
            "mode": "docker",
            "environment": {
                "CEDAR_IMAGE_PREFIX": "registry.example/cedar",
                "CEDAR_TERMINOLOGY_STORE_CATALOG": "",
            },
        }, json.loads(self.state_path.read_text()))


class ModeGateTest(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()
        self.runtime_patch = patch.object(
            ModeManager, "require_runtime_compatible", side_effect=lambda mode: mode)
        self.runtime_patch.start()
        self.docker_start_patch = patch.object(
            ModeManager, "require_docker_start_compatible", side_effect=lambda mode: mode)
        self.docker_start_patch.start()

    def tearDown(self):
        self.docker_start_patch.stop()
        self.runtime_patch.stop()

    @patch.object(ModeManager, "current", return_value=CedarMode.NATIVE)
    def test_native_mode_rejects_every_docker_command(self, _current):
        result = self.runner.invoke(docker.app, ["status"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("mode is native", result.output)

    @patch.object(ModeManager, "current", return_value=CedarMode.DOCKER)
    def test_docker_mode_rejects_every_native_command(self, _current):
        result = self.runner.invoke(native.app, ["status"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("mode is docker", result.output)

    @patch.object(StartFrontendWorker, "all")
    @patch.object(ModeManager, "current", return_value=CedarMode.HYBRID)
    def test_hybrid_allows_native_frontends(self, _current, start):
        result = self.runner.invoke(native.app, ["start", "frontends"])

        self.assertEqual(0, result.exit_code, result.output)
        start.assert_called_once_with()

    @patch.object(ModeManager, "current", return_value=CedarMode.HYBRID)
    def test_hybrid_rejects_native_backends(self, _current):
        result = self.runner.invoke(native.app, ["start", "microservices"])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("mode is hybrid", result.output)

    @patch.object(DockerWorker, "start_microservices", return_value=0)
    @patch.object(ModeManager, "current", return_value=CedarMode.HYBRID)
    def test_hybrid_allows_docker_backends(self, _current, start):
        result = self.runner.invoke(docker.app, [
            "start", "microservices", "--local",
        ])

        self.assertEqual(0, result.exit_code, result.output)
        start.assert_called_once_with(False, "never", None)

    @patch.object(DockerWorker, "start_frontends", return_value=0)
    @patch.object(ModeManager, "current", return_value=CedarMode.HYBRID)
    def test_hybrid_rejects_docker_frontends(self, _current, start):
        result = self.runner.invoke(docker.app, [
            "start", "frontends", "--local",
        ])

        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("Docker frontend start is not allowed", result.output)
        start.assert_not_called()

    @patch.object(DockerWorker, "stop_frontends", return_value=0)
    @patch.object(ModeManager, "current", return_value=CedarMode.HYBRID)
    def test_hybrid_can_stop_stale_docker_frontends(self, _current, stop):
        result = self.runner.invoke(docker.app, ["stop", "frontends"])

        self.assertEqual(0, result.exit_code, result.output)
        stop.assert_called_once_with()


class ModeRuntimeSafetyTest(unittest.TestCase):

    @patch.object(
        ModeManager, "running_native_services", return_value={"group", "ui-workspace"})
    def test_native_mode_cannot_be_cleared_while_applications_run(self, _native):
        with self.assertRaisesRegex(ModeError, "native stop all"):
            ModeManager.require_selected_services_stopped(CedarMode.NATIVE)

    @patch.object(
        ModeManager, "host_infrastructure_listeners",
        return_value={"mysql (port 3306, pid 123)"})
    @patch.object(ModeManager, "running_native_services", return_value=set())
    def test_native_mode_cannot_be_cleared_while_infrastructure_runs(
            self, _native, _infra):
        with self.assertRaisesRegex(ModeError, "native stop infra"):
            ModeManager.require_selected_services_stopped(CedarMode.NATIVE)

    @patch.object(
        ModeManager, "running_native_services", return_value={"ui-workspace", "resource"})
    def test_hybrid_mode_cannot_be_cleared_while_native_frontends_run(self, _native):
        with self.assertRaisesRegex(ModeError, "native stop frontends"):
            ModeManager.require_selected_services_stopped(CedarMode.HYBRID)

    @patch.object(ModeManager, "running_native_services")
    def test_docker_clear_does_not_treat_host_processes_as_docker_owned(self, native):
        self.assertEqual(
            CedarMode.DOCKER,
            ModeManager.require_selected_services_stopped(CedarMode.DOCKER),
        )
        native.assert_not_called()

    @patch.object(ModeManager, "running_native_services", return_value=set())
    @patch.object(DockerWorker, "running_compose_projects", return_value=set())
    @patch.object(
        DockerWorker, "active_deployment", return_value=DockerDeploymentMode.FULL)
    def test_native_rejects_a_recorded_docker_deployment(
            self, _active, _projects, _native):
        with self.assertRaisesRegex(ModeError, "Docker deployment is active \\(docker\\)"):
            ModeManager.require_runtime_compatible(CedarMode.NATIVE)

    @patch.object(ModeManager, "running_native_services", return_value=set())
    @patch.object(DockerWorker, "running_compose_projects", return_value=set())
    @patch.object(
        DockerWorker, "active_deployment", return_value=DockerDeploymentMode.FULL)
    def test_docker_can_adopt_its_matching_deployment_record(
            self, _active, _projects, _native):
        self.assertEqual(
            CedarMode.DOCKER,
            ModeManager.require_runtime_compatible(CedarMode.DOCKER),
        )

    @patch.object(ModeManager, "running_native_services", return_value=set())
    @patch.object(DockerWorker, "running_compose_projects", return_value=set())
    @patch.object(
        DockerWorker, "active_deployment", return_value=DockerDeploymentMode.HYBRID)
    def test_docker_rejects_a_hybrid_deployment_record(
            self, _active, _projects, _native):
        with self.assertRaisesRegex(ModeError, "conflicts with the active Docker deployment"):
            ModeManager.require_runtime_compatible(CedarMode.DOCKER)

    @patch.object(ModeManager, "running_native_services", return_value=set())
    @patch.object(
        DockerWorker, "running_compose_projects", return_value={"cedar-microservices"})
    @patch.object(DockerWorker, "active_deployment", return_value=None)
    def test_native_rejects_unrecorded_running_docker_projects(
            self, _active, _projects, _native):
        with self.assertRaisesRegex(ModeError, "cedar-microservices"):
            ModeManager.require_runtime_compatible(CedarMode.NATIVE)

    @patch.object(
        ModeManager, "running_native_services", return_value={"group", "ui-workspace"})
    @patch.object(ModeManager, "host_infrastructure_listeners", return_value=set())
    def test_docker_rejects_running_native_services(self, _infra, _native):
        with self.assertRaisesRegex(ModeError, "group, ui-workspace"):
            ModeManager.require_docker_start_compatible(CedarMode.DOCKER)

    @patch.object(
        ModeManager, "running_native_services", return_value={"ui-workspace", "ui-designer"})
    @patch.object(ModeManager, "host_infrastructure_listeners", return_value=set())
    def test_hybrid_allows_running_native_frontends(self, _infra, _native):
        self.assertEqual(
            CedarMode.HYBRID,
            ModeManager.require_docker_start_compatible(CedarMode.HYBRID),
        )

    @patch.object(
        ModeManager, "running_native_services", return_value={"ui-workspace", "resource"})
    @patch.object(ModeManager, "host_infrastructure_listeners", return_value=set())
    def test_hybrid_rejects_a_running_native_backend(self, _infra, _native):
        with self.assertRaisesRegex(ModeError, "resource"):
            ModeManager.require_docker_start_compatible(CedarMode.HYBRID)

    @patch.object(
        ModeManager, "host_infrastructure_listeners",
        return_value={"redis (port 6379, pid 456)"})
    @patch.object(ModeManager, "running_native_services", return_value=set())
    def test_docker_rejects_host_infrastructure_listeners(self, _native, _infra):
        with self.assertRaisesRegex(ModeError, "redis .*6379"):
            ModeManager.require_docker_start_compatible(CedarMode.DOCKER)

    @patch.object(
        DockerWorker, "running_compose_projects", return_value={"cedar-frontend"})
    @patch.object(DockerWorker, "active_deployment", return_value=None)
    def test_hybrid_rejects_stale_docker_frontend_project(self, _active, _projects):
        with self.assertRaisesRegex(ModeError, "docker stop frontends"):
            ModeManager.require_runtime_compatible(CedarMode.HYBRID)

    @patch.object(ModeManager, "require_runtime_compatible", side_effect=ModeError("mixed"))
    @patch.object(ModeManager, "current", return_value=CedarMode.NATIVE)
    def test_cleanup_surface_can_bypass_a_mixed_runtime(self, _current, runtime):
        self.assertEqual(
            CedarMode.NATIVE,
            ModeManager.require_surface("native", check_runtime=False),
        )
        runtime.assert_not_called()

    @patch.object(ModeManager, "apply_profile")
    def test_bootstrap_loads_cleanup_profile_without_runtime_gate(self, apply_profile):
        ModeManager.bootstrap(["docker", "stop", "all"])

        apply_profile.assert_called_once_with("docker", check_runtime=False)

    @patch.object(ModeManager, "apply_profile")
    def test_bootstrap_keeps_runtime_gate_for_start(self, apply_profile):
        ModeManager.bootstrap(["docker", "start", "all"])

        apply_profile.assert_called_once_with("docker", check_runtime=True)

    @patch.object(ModeManager, "apply_profile")
    def test_bootstrap_does_not_require_a_deployment_profile_for_image_builds(self, apply_profile):
        ModeManager.bootstrap(["docker", "build", "cedar-java", "--train", "2.9.3-dev.20260826.0606"])

        apply_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class NativeProfileTest(unittest.TestCase):
    """What the recorded profile promises, and what refuses a host that took the wrong one."""

    DEVELOP_ENVIRONMENT = {
        "CEDAR_KEYCLOAK_ALLOW_INSECURE_TLS": "true",
        "CEDAR_FRONTEND_TARGET": "local",
        "CEDAR_FRONTEND_local_UI_HOST": "metadatacenter.orgx",
        "CEDAR_FRONTEND_local_REST_HOST": "metadatacenter.orgx",
        "CEDAR_FRONTEND_local_USER1_LOGIN": "test1@test.com",
        "CEDAR_FRONTEND_local_USER2_LOGIN": "test2@test.com",
    }
    SERVER_ENVIRONMENT = {
        "CEDAR_KEYCLOAK_ALLOW_INSECURE_TLS": "false",
        "CEDAR_FRONTEND_TARGET": "server",
        "CEDAR_FRONTEND_server_UI_HOST": "metadatacenter.org",
        "CEDAR_FRONTEND_server_REST_HOST": "metadatacenter.org",
        "CEDAR_FRONTEND_server_USER1_LOGIN": "-",
        "CEDAR_FRONTEND_server_USER2_LOGIN": "-",
    }

    def test_each_profile_accepts_the_environment_it_produces(self):
        self.assertIs(CedarProfile.DEVELOP, ModeManager.require_profile_invariants(
            self.DEVELOP_ENVIRONMENT, CedarProfile.DEVELOP))
        self.assertIs(CedarProfile.SERVER, ModeManager.require_profile_invariants(
            self.SERVER_ENVIRONMENT, CedarProfile.SERVER))

    def test_a_server_may_not_bypass_keycloak_tls_verification(self):
        """The failure that put the workstation profile on a staging host, caught at mode time."""
        with self.assertRaises(ModeError) as refused:
            ModeManager.require_profile_invariants(
                self.DEVELOP_ENVIRONMENT, CedarProfile.SERVER)

        self.assertIn("bypasses Keycloak TLS verification", str(refused.exception))

    def test_a_workstation_needs_the_bypass_its_local_leaves_require(self):
        with self.assertRaises(ModeError) as refused:
            ModeManager.require_profile_invariants(
                self.SERVER_ENVIRONMENT, CedarProfile.DEVELOP)

        self.assertIn("not in any truststore", str(refused.exception))

    def test_the_frontend_settings_the_builds_require_must_all_be_present(self):
        incomplete = dict(self.SERVER_ENVIRONMENT)
        del incomplete["CEDAR_FRONTEND_server_USER2_LOGIN"]

        with self.assertRaises(ModeError) as refused:
            ModeManager.require_profile_invariants(incomplete, CedarProfile.SERVER)

        self.assertIn("CEDAR_FRONTEND_server_USER2_LOGIN", str(refused.exception))

    def test_a_server_may_not_run_on_template_placeholders(self):
        placeholder = dict(self.SERVER_ENVIRONMENT, CEDAR_NEO4J_USER_PASSWORD="changeme")

        with self.assertRaises(ModeError) as refused:
            ModeManager.require_profile_invariants(placeholder, CedarProfile.SERVER)

        self.assertIn("CEDAR_NEO4J_USER_PASSWORD", str(refused.exception))

    def test_a_workstation_may_carry_credentials_it_has_no_use_for(self):
        placeholder = dict(self.DEVELOP_ENVIRONMENT, CEDAR_NEO4J_USER_PASSWORD="changeme")

        self.assertIs(CedarProfile.DEVELOP, ModeManager.require_profile_invariants(
            placeholder, CedarProfile.DEVELOP))

    def test_a_host_recorded_before_profiles_existed_is_not_given_one(self):
        with patch.object(ModeManager, "state", return_value={"mode": CedarMode.NATIVE}):
            with self.assertRaises(ModeError) as refused:
                ModeManager.require_profile()

        self.assertIn("mode native --profile develop|server", str(refused.exception))

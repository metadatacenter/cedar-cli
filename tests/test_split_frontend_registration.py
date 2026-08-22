import unittest
from unittest.mock import patch

from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.config.ServersFactory import ServersFactory
from org.metadatacenter.model.ServerTag import ServerTag
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.planner.DeployPlanner import DeployPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker
from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker


class SplitFrontendRegistrationTest(unittest.TestCase):

    def test_split_repositories_are_independently_published(self):
        repos = ReposFactory.build_repos()

        for name in ("cedar-workspace", "cedar-template-designer"):
            repo = repos.map[name]
            self.assertTrue(repo.is_frontend)
            self.assertTrue(repo.skip_from_release)
            self.assertTrue(repo.skip_from_default_deploy)
            self.assertTrue(repo.allow_different_version)
            self.assertEqual(['npm ci'], repo.build_command_list)
            self.assertEqual(1, len(repo.server_build_command_list))
            self.assertIn('build-native-split-frontend.sh', repo.server_build_command_list[0])
            self.assertEqual('npm ci', repo.deploy_command_list[0])
            self.assertIn('publish-frontend-package.sh', repo.deploy_command_list[1])
            self.assertNotIn(repo, repos.get_release_all())
            self.assertNotIn(repo, repos.get_frontends_for_default_deploy())

        self.assertEqual(
            ["cedar-workspace", "cedar-template-designer"],
            [repo.name for repo in repos.get_split_frontends()])

    @patch.object(GlobalContext, "repos", new_callable=ReposFactory.build_repos)
    def test_split_build_and_publish_plans_are_explicit(self, repos):
        build_plan = Plan("Build split frontends")
        BuildPlanner.split_frontends(build_plan)
        deploy_plan = Plan("Publish split frontends")
        DeployPlanner.split_frontends(deploy_plan)
        default_deploy_plan = Plan("Deploy frontends")
        DeployPlanner.frontends(default_deploy_plan)

        expected = ["cedar-workspace", "cedar-template-designer"]
        self.assertEqual(expected, [task.repo.name for task in build_plan.tasks])
        self.assertEqual(expected, [task.repo.name for task in deploy_plan.tasks])
        self.assertTrue(all(name not in expected for name in
                            [task.repo.name for task in default_deploy_plan.tasks]))

        server_plan = Plan("Build native server payloads")
        BuildPlanner.split_frontends(server_plan, server_payload=True)
        self.assertTrue(all(task.parameters["server_frontend_payload"]
                            for task in server_plan.tasks))

    def test_split_processes_are_non_essential_previews(self):
        servers = ServersFactory.build_servers()

        self.assertEqual(4201, servers.map["workspace"].port)
        self.assertEqual(4202, servers.map["designer"].port)
        self.assertEqual(ServerTag.FRONTEND_NON_ESSENTIAL, servers.map["workspace"].tag)
        self.assertEqual(ServerTag.FRONTEND_NON_ESSENTIAL, servers.map["designer"].tag)

    @patch("org.metadatacenter.worker.StartFrontendWorker.Worker.execute_generic_shell_commands")
    def test_preview_start_commands_delegate_to_shared_service_controller(self, execute):
        StartFrontendWorker.workspace()
        StartFrontendWorker.designer()

        self.assertIn("start-frontend-workspace.sh", execute.call_args_list[0].args[0][0])
        self.assertIn("start-frontend-designer.sh", execute.call_args_list[1].args[0][0])

    @patch("org.metadatacenter.worker.StopFrontendWorker.Worker.execute_generic_shell_commands")
    def test_preview_stop_commands_delegate_to_shared_service_controller(self, execute):
        StopFrontendWorker.workspace()
        StopFrontendWorker.designer()

        self.assertIn("stop-frontend-workspace.sh", execute.call_args_list[0].args[0][0])
        self.assertIn("stop-frontend-designer.sh", execute.call_args_list[1].args[0][0])

    @patch("org.metadatacenter.worker.StartFrontendWorker.Worker.execute_generic_shell_commands")
    def test_split_start_command_starts_both_native_services(self, execute):
        StartFrontendWorker.split_frontends()

        self.assertEqual(2, execute.call_count)
        self.assertIn("start-frontend-workspace.sh", execute.call_args_list[0].args[0][0])
        self.assertIn("start-frontend-designer.sh", execute.call_args_list[1].args[0][0])

    @patch("org.metadatacenter.worker.StopFrontendWorker.Worker.execute_generic_shell_commands")
    def test_split_stop_command_stops_both_native_services(self, execute):
        StopFrontendWorker.split_frontends()

        self.assertEqual(2, execute.call_count)
        self.assertIn("stop-frontend-workspace.sh", execute.call_args_list[0].args[0][0])
        self.assertIn("stop-frontend-designer.sh", execute.call_args_list[1].args[0][0])


if __name__ == "__main__":
    unittest.main()

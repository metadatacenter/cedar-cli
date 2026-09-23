import unittest
from unittest.mock import patch

from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.config.ServersFactory import ServersFactory
from org.metadatacenter.model.ServerTag import ServerTag
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.BuildOperator import BuildOperator
from org.metadatacenter.operator.PublishOperator import PublishOperator
from org.metadatacenter.taskfactory.PublishShellTaskFactory import NPM_PUBLISH
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker
from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker


@patch.dict("os.environ", {"CEDAR_HOME": "/tmp/CEDAR"})
class SplitFrontendRegistrationTest(unittest.TestCase):

    def test_split_repositories_are_platform_release_members(self):
        repos = ReposFactory.build_repos()

        for name in ("cedar-workspace", "cedar-template-designer"):
            repo = repos.map[name]
            self.assertTrue(repo.is_frontend)
            self.assertFalse(repo.skip_from_release)
            self.assertFalse(repo.allow_different_version)
            self.assertEqual(['npm ci', 'npm run build'] if name == 'cedar-workspace' else ['npm ci'], repo.build_command_list)
            self.assertEqual(1, len(repo.server_build_command_list))
            self.assertIn('build-native-split-frontend.sh', repo.server_build_command_list[0])
            self.assertEqual([NPM_PUBLISH] if name == 'cedar-workspace' else None,
                             repo.publish_command_list)
            self.assertIn(repo, repos.get_release_all())
            self.assertIn(repo, repos.get_frontends())

        self.assertEqual(
            ["cedar-workspace", "cedar-template-designer"],
            [repo.name for repo in repos.get_split_frontends()])

    @patch.object(GlobalContext, "repos", new_callable=ReposFactory.build_repos)
    def test_split_native_build_plan_is_explicit(self, repos):
        build_plan = Plan("Build split frontends")
        BuildPlanner.split_frontends(build_plan)

        expected = ["cedar-workspace", "cedar-template-designer"]
        self.assertEqual(expected, [task.repo.name for task in build_plan.tasks])

        server_plan = Plan("Build native server payloads")
        BuildPlanner.split_frontends(server_plan, server_payload=True)
        self.assertTrue(all(task.parameters["server_frontend_payload"]
                            for task in server_plan.tasks))
        for task in server_plan.tasks:
            self.assertIn("build-native-split-frontend.sh",
                          task.repo.server_build_command_list[0])

    @patch.object(GlobalContext, "repos", new_callable=ReposFactory.build_repos)
    def test_workspace_builds_angular_and_preserves_native_deployment(self, repos):
        repo = repos.map["cedar-workspace"]
        self.assertEqual(RepoType.ANGULAR, repo.repo_type)
        for deployment in (False, True):
            plan = Plan("Workspace build")
            BuildPlanner.split_frontends(plan, server_payload=deployment)
            task = plan.tasks[0]
            BuildOperator.expand(task)
            build = task.tasks[0].tasks[0]
            if deployment:
                self.assertIn("build-native-split-frontend.sh", build.command_list[0])
                self.assertTrue(build.parameters["in_place_frontend_build"])
                self.assertNotIn("isolated_frontend_build", build.parameters)
            else:
                self.assertEqual(["npm ci", "npm run build"], build.command_list)
                self.assertTrue(build.parameters["isolated_frontend_build"])

    @patch.object(GlobalContext, "repos", new_callable=ReposFactory.build_repos)
    def test_angular_publication_builds_staged_output_before_explicit_publish(self, repos):
        for name, expected in (
            ("cedar-workspace", ['npm ci', 'npm run build', NPM_PUBLISH]),
            ("cedar-embeddable-term-picker", ['npm ci', 'npm run dist',
                'npm publish ./dist-npm/cedar-embeddable-term-picker --tag=dev']),
        ):
            with self.subTest(repository=name):
                task = PlanTask("Publish", TaskType.PUBLISH, repos.map[name])
                PublishOperator.expand(task)
                commands = [command for wrapper in task.tasks for child in wrapper.tasks
                            for command in child.command_list]
                self.assertEqual(expected, commands)

    @patch.object(GlobalContext, "repos", new_callable=ReposFactory.build_repos)
    def test_angular_source_without_explicit_publication_stays_build_only(self, repos):
        repo = repos.map["cedar-content-distribution"]
        task = PlanTask("Publish", TaskType.PUBLISH, repo)
        PublishOperator.expand(task)
        commands = [command for wrapper in task.tasks for child in wrapper.tasks
                    for command in child.command_list]
        self.assertEqual(['npm ci --legacy-peer-deps', 'ng build --configuration=production'], commands)

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

        self.assertIn("cedar-services.sh start ui-workspace", execute.call_args_list[0].args[0][0])
        self.assertIn("cedar-services.sh start ui-designer", execute.call_args_list[1].args[0][0])

    @patch("org.metadatacenter.worker.StopFrontendWorker.Worker.execute_generic_shell_commands")
    def test_preview_stop_commands_delegate_to_shared_service_controller(self, execute):
        StopFrontendWorker.workspace()
        StopFrontendWorker.designer()

        self.assertIn("cedar-services.sh stop ui-workspace", execute.call_args_list[0].args[0][0])
        self.assertIn("cedar-services.sh stop ui-designer", execute.call_args_list[1].args[0][0])

    @patch("org.metadatacenter.worker.StartFrontendWorker.Worker.execute_generic_shell_commands")
    def test_split_start_command_starts_both_native_services(self, execute):
        StartFrontendWorker.split_frontends()

        self.assertEqual(1, execute.call_count)
        self.assertIn("cedar-services.sh start ui-workspace ui-designer", execute.call_args.args[0][0])

    @patch("org.metadatacenter.worker.StopFrontendWorker.Worker.execute_generic_shell_commands")
    def test_split_stop_command_stops_both_native_services(self, execute):
        StopFrontendWorker.split_frontends()

        self.assertEqual(1, execute.call_count)
        self.assertIn("cedar-services.sh stop ui-workspace ui-designer", execute.call_args.args[0][0])


if __name__ == "__main__":
    unittest.main()

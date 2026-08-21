import unittest
from unittest.mock import patch

from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.config.ServersFactory import ServersFactory
from org.metadatacenter.model.ServerTag import ServerTag
from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker
from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker


class SplitFrontendRegistrationTest(unittest.TestCase):

    def test_split_repositories_are_preview_only(self):
        repos = ReposFactory.build_repos()

        for name in ("cedar-workspace", "cedar-template-designer"):
            repo = repos.map[name]
            self.assertTrue(repo.is_frontend)
            self.assertTrue(repo.skip_from_release)
            self.assertTrue(repo.allow_different_version)
            self.assertNotIn(repo, repos.get_release_all())

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


if __name__ == "__main__":
    unittest.main()

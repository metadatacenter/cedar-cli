import unittest

from org.metadatacenter.config.ReposFactory import ReposFactory


class ServerFrontendRegistrationTest(unittest.TestCase):
    """
    The monolith's served tree is built by the same command as the split applications'.

    Left out of it, the ANGULAR_JS branch falls back to a bare `npm ci` in an isolated checkout,
    which installs dependencies for a tree nginx does not serve. The real build was then typed by
    hand on every deploy, which is also why the monolith is the only served payload with no
    build-info identity.
    """

    def setUp(self):
        self.repos = ReposFactory.build_repos()

    def test_the_group_holds_all_three_natively_served_frontends(self):
        names = [repo.name for repo in self.repos.get_server_frontends()]
        self.assertEqual(
            ["cedar-template-editor", "cedar-workspace", "cedar-template-designer"], names)

    def test_the_monolith_has_a_server_payload_build(self):
        editor = self.repos.map["cedar-template-editor"]
        self.assertTrue(editor.server_build_command_list)
        command = " ".join(editor.server_build_command_list)
        self.assertIn("build-native-split-frontend.sh", command)
        self.assertIn("editor", command)

    def test_every_server_frontend_declares_its_own_payload_build(self):
        for repo in self.repos.get_server_frontends():
            with self.subTest(repo=repo.name):
                self.assertTrue(
                    repo.server_build_command_list,
                    f"{repo.name} is served from its own checkout but declares no server build")


if __name__ == "__main__":
    unittest.main()

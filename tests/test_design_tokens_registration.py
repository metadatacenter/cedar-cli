import unittest
from unittest.mock import patch

from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.VersionType import VersionType


@patch.dict("os.environ", {"CEDAR_HOME": "/tmp/CEDAR"})
class DesignTokensRegistrationTest(unittest.TestCase):

    def test_design_tokens_publishes_itself_outside_the_release(self):
        repos = ReposFactory.build_repos()
        tokens = repos.map["cedar-design-tokens"]

        self.assertEqual(RepoType.TYPESCRIPT, tokens.repo_type)
        self.assertEqual(
            [VersionType.PACKAGE_OWN, VersionType.PACKAGE_LOCK_OWN, VersionType.PACKAGE_LOCK_PACKAGES_OWN],
            tokens.version_list)
        # Its own cadence, like the TypeScript model library: a token change is
        # published when it is made rather than when the platform is released.
        self.assertTrue(tokens.skip_from_release)
        self.assertTrue(tokens.allow_different_version)
        # No pipeline of its own to drive. `npm ci` and `npm run build` are what the
        # repository type already implies, and the build is what emits the custom
        # properties a consumer imports.
        self.assertIsNone(tokens.build_command_list)
        self.assertIsNone(tokens.publish_command_list)
        self.assertIn(tokens, repos.get_frontends())

    def test_design_tokens_is_built_and_published_before_its_consumers(self):
        """The ordering rule, which only the position in ReposFactory enforces.

        Every npm consumer resolves the tokens from Nexus when its own build starts,
        so one built before this package publishes reads the previous snapshot and
        renders the previous values. A plan is walked in registration order, which
        makes that order load-bearing rather than tidy.
        """
        names = [repo.name for repo in ReposFactory.build_repos().get_frontends()]
        tokens = names.index("cedar-design-tokens")

        for consumer in ("cedar-embeddable-editor", "cedar-content-distribution",
                         "cedar-model-typescript-library"):
            self.assertLess(tokens, names.index(consumer),
                            f"cedar-design-tokens must be registered before {consumer}")


if __name__ == "__main__":
    unittest.main()

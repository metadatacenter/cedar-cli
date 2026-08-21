import unittest

from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.config.ServersFactory import ServersFactory
from org.metadatacenter.config.SubdomainsFactory import SubdomainsFactory


class RetiredFrontendsTest(unittest.TestCase):
    def test_retired_frontends_are_not_managed(self):
        repo_names = {repo.name for repo in ReposFactory.build_repos().get_list_all()}

        self.assertTrue({
            "cedar-artifacts",
            "cedar-artifacts-src",
            "cedar-artifacts-dist",
            "cedar-artifact-viewer",
        }.isdisjoint(repo_names))
        self.assertNotIn("artifacts", ServersFactory.build_servers().map)
        self.assertNotIn("artifacts", SubdomainsFactory.build_subdomains().map)


if __name__ == "__main__":
    unittest.main()

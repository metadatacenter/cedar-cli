import unittest
from unittest.mock import patch

from org.metadatacenter.config.SubdomainsFactory import SubdomainsFactory
from org.metadatacenter.worker.DevWorker import DevWorker


class DevHostsTest(unittest.TestCase):

    @patch('org.metadatacenter.worker.DevWorker.Worker.execute_generic_shell_commands')
    def test_add_hosts_uses_the_central_subdomain_inventory(self, execute):
        DevWorker.add_hosts()

        command = execute.call_args.args[0][0]
        configured = {name for name in SubdomainsFactory.build_subdomains().map if name}
        for name in configured:
            self.assertIn(f'    "{name}"', command)

        self.assertIn('    "workspace"', command)
        self.assertIn('    "designer"', command)
        self.assertIn('    "shared"', command)


if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import patch
from typer.testing import CliRunner
import cedar
from org.metadatacenter.worker.Worker import CommandOutput


class UtilityFailureTest(unittest.TestCase):
    @patch('org.metadatacenter.worker.Worker.Worker.execute_generic_shell_commands')
    def test_utility_commands_propagate_failures(self, execute):
        execute.return_value = CommandOutput([], 17)
        for args in (['cheat'], ['build', 'maven', 'clean', 'all'],
                     ['build', 'maven', 'clean', 'cedar']):
            result = CliRunner().invoke(cedar.app, args)
            self.assertEqual(17, result.exit_code, result.output)

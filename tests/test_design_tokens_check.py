import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner
from org.metadatacenter import check, design_tokens


class DesignTokensCommandTest(unittest.TestCase):
    @patch('org.metadatacenter.design_tokens.check_design_tokens', return_value=1)
    def test_command_preserves_failure_and_options(self, run):
        result = CliRunner().invoke(check.app, ['design-tokens', '--strict', '--json', '--repo', 'cedar-embeddable-designer'])
        self.assertEqual(1, result.exit_code)
        run.assert_called_once_with(repos=['cedar-embeddable-designer'], strict=True, json_output=True,
                                    show_all=False, init_baseline=False, prune_baseline=False)

    def test_missing_tool_is_not_success(self):
        with tempfile.TemporaryDirectory() as root, patch.object(design_tokens.Util, 'cedar_home', root):
            self.assertEqual(2, design_tokens.check_design_tokens())

    @patch('org.metadatacenter.design_tokens.subprocess.run')
    def test_invokes_shared_checker_without_a_shell(self, run):
        with tempfile.TemporaryDirectory() as root, patch.object(design_tokens.Util, 'cedar_home', root):
            path = Path(root) / 'cedar-design-tokens/tools/check_adoption.py'
            path.parent.mkdir(parents=True)
            path.touch()
            run.return_value.returncode = 2
            self.assertEqual(2, design_tokens.check_design_tokens(repos=['cedar-embeddable-designer'], prune_baseline=True))
            args = run.call_args.args[0]
            self.assertIn(str(path), args)
            self.assertEqual(['--repo', 'cedar-embeddable-designer', '--prune-baseline'], args[-3:])


if __name__ == '__main__':
    unittest.main()

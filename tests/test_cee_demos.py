from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from org.metadatacenter import build, cee_demos, test_processes
from org.metadatacenter.frontend_inventory import inventory
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context
from org.metadatacenter.util.Util import Util


class DemoCommandsTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(use_context(InvocationContext(environment=dict(os.environ))))
        recipes = inventory()
        stack.enter_context(patch('org.metadatacenter.frontend_inventory.inventory', return_value=recipes))
        stack.enter_context(patch.object(build, 'check_design_tokens', return_value=0))
        GlobalContext.init_task_operators()
        self.runner = CliRunner()

    def test_everyday_build_graph_omits_all_demos_but_keeps_other_frontends(self):
        for command in ('frontends', 'all'):
            with patch.object(build.plan_executor, 'execute') as execute:
                result = self.runner.invoke(build.app, [command, '--dry-run'])
            self.assertEqual(0, result.exit_code, result.output)
            def names(task):
                return ([task.repo.name] if getattr(task, 'repo', None) else []) + [
                    name for child in task.tasks for name in names(child)]
            repositories = names(execute.call_args.args[0])
            self.assertFalse(any('demo' in name for name in repositories))
            for name in ('cedar-embeddable-editor', 'cedar-workspace', 'cedar-embeddable-designer'):
                self.assertIn(name, repositories)

    def test_explicit_command_forces_all_demos_and_propagates_failure(self):
        with patch.object(cee_demos, 'run_demos', return_value=7) as run:
            result = self.runner.invoke(test_processes.app, ['cee-demos', '--cee-package', '/tmp/cee.tgz'])
        self.assertEqual(7, result.exit_code)
        run.assert_called_once_with(Path('/tmp/cee.tgz'))

    def test_conditional_failure_prevents_runtime_activation(self):
        with patch.object(build, 'execute_build', return_value={'cedar-embeddable-editor': 'hash'}), \
                patch.object(cee_demos, 'check_selection', side_effect=SystemExit(7)), \
                patch.object(build.reactor, 'activate_runtime') as activate:
            result = self.runner.invoke(build.app, ['frontends'])
        self.assertEqual(7, result.exit_code)
        activate.assert_not_called()

    def test_test_enabled_build_all_checks_demos_but_compile_only_does_not(self):
        with patch.object(build, 'execute_build', return_value={'cedar-embeddable-editor': 'hash'}), \
                patch.object(cee_demos, 'check_selection') as check:
            result = self.runner.invoke(build.app, ['all'])
            self.assertEqual(0, result.exit_code, result.output)
            check.assert_called_once_with({'cedar-embeddable-editor': 'hash'})
            check.reset_mock()
            result = self.runner.invoke(build.app, ['all', '--skip-tests'])
            self.assertEqual(0, result.exit_code, result.output)
            check.assert_not_called()

    def test_runner_receives_candidate_consumer_pins_and_profile(self):
        with tempfile.TemporaryDirectory() as home, \
                patch.object(Util, 'cedar_home', home), \
                patch.object(cee_demos, 'require_build_node'), \
                patch.object(cee_demos.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertEqual(0, cee_demos.run_demos(Path(home) / 'cee.tgz', if_changed=True))
            command = run.call_args.args[0]
            self.assertIn('--if-changed', command)
            for name in ('cedar-workspace', 'cedar-template-editor', 'cedar-component-demo'):
                self.assertTrue(any(str(Path(home) / name) in arg for arg in command))
            self.assertIn('PATH', run.call_args.kwargs['env'])


if __name__ == '__main__':
    unittest.main()

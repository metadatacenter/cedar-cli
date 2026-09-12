import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import typer

from org.metadatacenter import build
from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
from org.metadatacenter.util.BuildSafety import BuildSafetyError
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context
from org.metadatacenter.util.NodeBuildCheck import require_build_node, require_plan_node


class NodeBuildCheckTest(unittest.TestCase):
    def fixture(self, root, version):
        root = Path(root)
        binary = root / 'node'
        binary.write_text(f'#!/bin/sh\nprintf "%s\\n" "{version}"\n')
        binary.chmod(0o755)
        project = root / 'project'
        project.mkdir(exist_ok=True)
        return project, {**os.environ, 'PATH': str(root)}

    def test_staging_node_fails_with_required_actual_and_executable(self):
        with tempfile.TemporaryDirectory() as root:
            project, env = self.fixture(root, 'v16.20.2')
            (project / '.nvmrc').write_text('24.19.0\n')
            with self.assertRaises(BuildSafetyError) as error:
                require_build_node(project, env)
            message = str(error.exception)
            for expected in ('24.19.0', 'v16.20.2', str(Path(root) / 'node'), '.nvmrc'):
                self.assertIn(expected, message)

    def test_project_pin_and_shared_release_pin(self):
        with tempfile.TemporaryDirectory() as root:
            project, env = self.fixture(root, 'v24.19.0')
            require_build_node(project, env)
            (project / '.nvmrc').write_text('v22.22.3\n')
            with self.assertRaisesRegex(BuildSafetyError, 'requires Node 22.22.3'):
                require_build_node(project, env)
            self.fixture(root, 'v22.22.3')
            require_build_node(project, env)

    def test_missing_node_and_invalid_pin_fail_clearly(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(BuildSafetyError, 'not installed'):
                require_build_node(root, {'PATH': root})
            (Path(root) / '.nvmrc').write_text('lts/*')
            with self.assertRaisesRegex(BuildSafetyError, 'exact Node version'):
                require_build_node(root, {'PATH': root})

    def test_mixed_plan_fails_before_any_task_or_snapshot(self):
        frontend = SimpleNamespace(parameters={'isolated_frontend_build': True},
                                   repo=Mock(), tasks=[])
        java = SimpleNamespace(parameters={}, tasks=[])
        plan = SimpleNamespace(tasks=[java, frontend])
        with tempfile.TemporaryDirectory() as root:
            project, env = self.fixture(root, 'v16.20.2')
            with use_context(InvocationContext(environment=env)), \
                    patch('org.metadatacenter.util.NodeBuildCheck.Util.get_wd', return_value=str(project)), \
                    patch.object(build.plan_executor, 'execute') as execute, \
                    patch.object(build, 'capture_estate_state') as snapshot:
                with self.assertRaises(typer.Exit):
                    build.execute_build(plan, False, False)
                execute.assert_not_called()
                snapshot.assert_not_called()
                build.execute_build(plan, True, False)
                execute.assert_called_once_with(plan, True, False)

    def test_java_only_and_skipped_frontends_do_not_require_node(self):
        plan = SimpleNamespace(tasks=[SimpleNamespace(parameters={}, tasks=[])])
        with patch('org.metadatacenter.util.NodeBuildCheck.require_build_node') as check:
            require_plan_node(plan)
            check.assert_not_called()

    def test_both_frontend_paths_stop_before_copy_or_install(self):
        with tempfile.TemporaryDirectory() as root:
            project, env = self.fixture(root, 'v16.20.2')
            for flag in ('isolated_frontend_build', 'in_place_frontend_build'):
                task = SimpleNamespace(parameters={flag: True}, node_id=1,
                                       repo=SimpleNamespace(name='test', repo_type='typescript'),
                                       command_list=['npm ci', 'npm run build'])
                executor = ShellTaskExecutor()
                with use_context(InvocationContext(environment=env)), \
                        patch('org.metadatacenter.taskexecutor.ShellTaskExecutor.Util.get_wd',
                              return_value=str(project)), \
                        patch.object(executor, '_execute_commands') as commands, \
                        patch('org.metadatacenter.taskexecutor.ShellTaskExecutor.isolated_frontend_workspace') as copy:
                    self.assertEqual(1, executor.execute_shell_command_list(task, Mock(), False))
                    commands.assert_not_called()
                    copy.assert_not_called()

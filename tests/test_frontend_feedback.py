from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from typer.testing import CliRunner
from org.metadatacenter import build, test_processes
from org.metadatacenter.frontend_inventory import reactor_checks
from org.metadatacenter.frontend_timings import frontend_timings
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context


class FrontendFeedbackTest(unittest.TestCase):
    def test_retry_success_marks_even_a_frontend_without_task_cache_definitions(self):
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
        with tempfile.TemporaryDirectory() as directory:
            def run(*_args, **kwargs):
                kwargs['on_line']('\x1b[33m1 flaky\x1b[0m')
                return SimpleNamespace(returncode=0)
            repo = SimpleNamespace(name='cedar-template-designer', repo_type='angular_js')
            task = SimpleNamespace(node_id=1)
            with patch('org.metadatacenter.taskexecutor.ShellTaskExecutor.run_shell', side_effect=run):
                ShellTaskExecutor().execute_shell_command(
                    task, repo, 'npm test', directory, Mock(),
                    environment={'CEDAR_BUILD_WORKSPACE_ROOT': directory})
            self.assertTrue((Path(directory) / '.cedar-cache-unstable').is_file())

    def test_verification_keeps_one_build_before_browser_checks(self):
        checks = reactor_checks()
        for repo in ['cedar-workspace', 'cedar-openview-src', 'cedar-monitoring-src', 'cedar-bridging-src']:
            commands = checks[repo].commands
            self.assertEqual(1, commands.count('npm run build'), (repo, commands))
            self.assertLess(commands.index('npm run build'), len(commands) - 1)
        self.assertIn('npm run test:ci:prebuilt', checks['cedar-model-typescript-library'].commands)

    def test_feedback_command_defaults_and_never_deploys(self):
        with use_context(InvocationContext()), \
                patch('org.metadatacenter.util.InvocationContext.os.cpu_count', return_value=16), \
                patch.object(build, 'run_frontends') as run:
            result = CliRunner().invoke(test_processes.app, ['frontends', '--no-reuse'])
            self.assertEqual(0, result.exit_code, result.output)
            from org.metadatacenter.util.InvocationContext import current_context
            self.assertEqual(3, current_context().settings.build_jobs)
            self.assertEqual(12, current_context().settings.build_workers)
            run.assert_called_once_with(dry_run=False, dump_plan=False, reuse=False, deploy=False)

    def test_checks_include_consumers_but_cannot_activate_restart_or_smoke(self):
        with tempfile.TemporaryDirectory() as home, ExitStack() as stack:
            stack.enter_context(patch.object(build.Util, 'cedar_home', home))
            stack.enter_context(patch.object(build.BuildPlanner, 'frontends'))
            stack.enter_context(patch.object(build, 'require_design_token_adoption'))
            stack.enter_context(patch.object(build, 'execute_build', return_value=SimpleNamespace(evidence='e')))
            demos = stack.enter_context(patch('org.metadatacenter.cee_demos.check_selection'))
            activate = stack.enter_context(patch.object(build.reactor, 'activate_runtime'))
            restart = stack.enter_context(patch.object(build.NativeWorker, 'restart'))
            smoke = stack.enter_context(patch.object(build, 'run_smoke'))
            build.run_frontends(deploy=False)
            demos.assert_called_once()
            activate.assert_not_called()
            restart.assert_not_called()
            smoke.assert_not_called()
            report = json.loads(next(Path(home).glob('.cedar/build-reports/frontend-runs/*.json')).read_text())
            self.assertTrue(report['passed'])
            self.assertEqual('checks', report['kind'])

    def test_failed_phase_is_timed_and_never_reported_as_a_pass(self):
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaisesRegex(RuntimeError, 'broken'):
                with frontend_timings(home, 'reactor') as timing:
                    with timing.phase('build'):
                        raise RuntimeError('broken')
            report = json.loads(next(Path(home).glob('.cedar/build-reports/frontend-runs/*.json')).read_text())
            self.assertFalse(report['passed'])
            self.assertFalse(report['phases'][0]['passed'])

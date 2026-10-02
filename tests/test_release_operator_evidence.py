from contextlib import contextmanager
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from org.metadatacenter.build_diagnostics import failure_diagnostics, retain_failure, COMMAND_LOG
from org.metadatacenter.release_support import presentation
from org.metadatacenter.release_support.preflight import ReleasePreflight
from org.metadatacenter.release_support.state import ReleaseState
from org.metadatacenter.train_support import release_intent


class OperatorEvidenceTest(unittest.TestCase):
    def test_intent_runs_remote_survey_and_preserves_remedy(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / 'cedar-development/ops/build-train.json'
            config.parent.mkdir(parents=True)
            config.write_text('{"repositories": ["repo"]}')
            called = []
            def check(checker, names):
                called.extend(names)
                self.assertEqual({'repo'}, checker.accepted_main_only)
                from org.metadatacenter.release_support.preflight import PreflightFinding
                return [PreflightFinding('remote', 'fail', 'main-only source', 'review the source')]
            with patch.object(release_intent.ReleasePlanner, '_release_repositories', return_value=(['repo'], [])), \
                 patch.object(release_intent.ReleasePlanner, '_maven_phases', return_value=[]), \
                 patch.object(release_intent.ReleasePlanner, '_publication_plan', return_value={}), \
                 patch.object(ReleasePreflight, '_run_checks', check):
                with self.assertRaisesRegex(ValueError, 'review the source'):
                    release_intent.preflight({'releaseVersion': '1.0.0'},
                        source={'repositories': {'repo': 'a' * 40}},
                        environment={'CEDAR_HOME': directory}, accepted_main_only={'repo'})
            self.assertIn('check_remote_survey', called)
            self.assertIn('check_license_files', called)

    def test_generated_distribution_classification_does_not_bypass_gate(self):
        checker = ReleasePreflight({'releaseRepositories': ['cedar-openview']})
        with patch('org.metadatacenter.release_support.preflight.ReleaseRemoteIntegrator.survey',
                   return_value={'cedar-openview': ['cedar-openview-dist/main-old.js',
                                                  'cedar-openview-dist/README.md', 'src/hotfix.ts']}):
            finding = checker.check_remote_survey()[0]
        self.assertTrue(finding.fatal)
        self.assertIn('declared generated distribution', finding.message)
        self.assertIn('README.md [source or unclassified', finding.message)
        self.assertIn('hotfix.ts [source or unclassified', finding.message)

    def test_lock_drives_running_status_and_resume_advice(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReleaseState(root=Path(directory))
            manifest = {'releaseVersion': '1.0.0', 'phase': 'started'}
            path = state.manifest_path('1.0.0')
            self.assertFalse(state.controller_running())
            with state.exclusive():
                self.assertTrue(state.controller_running())
                with presentation.console.capture() as capture:
                    presentation._render_release_status(manifest, path)
                self.assertIn('Controller: running', capture.get())
                self.assertNotIn('Run:  cedarcli release resume', capture.get())
            self.assertFalse(state.controller_running())
            with presentation.console.capture() as capture:
                presentation._render_release_status(manifest, path)
            self.assertIn('Controller: stopped', capture.get())
            self.assertIn('Run:  cedarcli release resume', capture.get())

    def test_heartbeat_reports_without_mutating_state_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReleaseState(root=Path(directory))
            state.start({'releaseVersion': '1.0.0', 'phase': 'started'})
            before = state.read_current_manifest()[0]
            seen = threading.Event()
            with patch.object(presentation.console, 'print', side_effect=lambda *a, **k: seen.set()) as output:
                with presentation.release_heartbeat(state, interval=.01):
                    self.assertTrue(seen.wait(2))
                count = output.call_count
                self.assertFalse(any(t.name == 'release-progress' for t in threading.enumerate()))
                self.assertEqual(count, output.call_count)
            self.assertEqual(before, state.read_current_manifest()[0])

    def test_failure_reports_survive_cleanup_exclude_dependencies_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                for path in ['browser/test-results/trace.zip', 'target/surefire-reports/test.xml',
                             'node_modules/test-results/secret', '.npmrc']:
                    target = work / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text('evidence')
                (work / 'browser/test-results/link').symlink_to(work / '.npmrc')
                (work / COMMAND_LOG).write_text('failed command')
                reports = []
                with failure_diagnostics(work, home, lambda line, **kw: reports.append(line)) as outcome:
                    outcome['exitCode'] = 1
                retained = next((home / '.cedar/build-reports/failures').iterdir())
            self.assertTrue((retained / 'browser/test-results/trace.zip').exists())
            self.assertTrue((retained / 'target/surefire-reports/test.xml').exists())
            self.assertFalse((retained / 'browser/test-results/link').exists())
            self.assertFalse((retained / 'node_modules').exists())
            self.assertFalse((retained / '.npmrc').exists())
            self.assertIn(str(retained), reports[0])

    def test_diagnostics_bound_storage_and_do_not_retain_success(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            work = home / 'work'
            (work / 'test-results').mkdir(parents=True)
            (work / 'test-results/large.zip').write_bytes(b'x' * 11)
            with failure_diagnostics(work, home, lambda *a, **k: None) as outcome:
                outcome['exitCode'] = 0
            self.assertFalse((home / '.cedar').exists())
            retained = retain_failure(work, home, limit=10)
            manifest = json.loads((retained / 'manifest.json').read_text())
            self.assertEqual(0, manifest['bytes'])
            self.assertEqual(['test-results/large.zip'], manifest['omittedForSize'])

    def test_diagnostics_retained_on_exception_without_masking_it(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / 'work'
            work.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'original'):
                with failure_diagnostics(work, directory, lambda *a, **k: None):
                    raise RuntimeError('original')
            self.assertEqual(1, len(list((Path(directory) / '.cedar/build-reports/failures').iterdir())))

    def test_executor_retains_failure_before_isolated_copy_is_removed(self):
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
        module = 'org.metadatacenter.taskexecutor.ShellTaskExecutor'
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            @contextmanager
            def isolated(_source):
                with tempfile.TemporaryDirectory() as temporary:
                    yield Path(temporary), {}, []
            task = SimpleNamespace(node_id=1, repo=SimpleNamespace(name='example', repo_type='TYPESCRIPT'),
                command_list=['failing test'], get_parameter=lambda name: name == 'isolated_frontend_build')
            def fail(task, repo, commands, cwd, progress, environment):
                log = Path(environment['CEDAR_BUILD_DIAGNOSTIC_LOG'])
                log.write_text('actual command failure')
                (Path(cwd) / 'test-results').mkdir()
                (Path(cwd) / 'test-results/failure.txt').write_text('failure detail')
                return 7
            executor = ShellTaskExecutor()
            with patch(module + '.Util.cedar_home', directory), \
                 patch(module + '.Util.get_wd', return_value=directory), \
                 patch(module + '.is_frontend_build', return_value=False), \
                 patch(module + '.reactor_evidence.begin'), \
                 patch(module + '.reactor.resolve', return_value=[]), \
                 patch(module + '.reactor.prepare_checks', return_value=[]), \
                 patch(module + '.isolated_frontend_workspace', isolated), \
                 patch.object(executor, '_execute_commands', side_effect=fail):
                self.assertEqual(7, executor.execute_shell_command_list(task, Mock(), False))
            retained = next((home / '.cedar/build-reports/failures').iterdir())
            self.assertEqual('actual command failure', (retained / COMMAND_LOG).read_text())
            self.assertEqual('failure detail', (retained / 'test-results/failure.txt').read_text())
            self.assertFalse(Path(json.loads((retained / 'manifest.json').read_text())['workspace']).exists())

    def test_diagnostic_configuration_error_does_not_mask_build_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = []
            with failure_diagnostics(directory, None, lambda line, **kw: reports.append(line)) as outcome:
                outcome['exitCode'] = 7
            self.assertIn('Could not retain', reports[0])

    def test_build_progress_links_each_parallel_task_log(self):
        manifest = {'releaseVersion': '1.0.0', 'phase': 'validating-builds',
            'frontendPreparation': {'workspace': '/tmp/attempt/frontend'},
            'buildValidation': {'attempt': 2, 'inProgressTasks': ['release:npm:one', 'release:npm:two']}}
        with patch.object(presentation, '_release_progress', return_value=[
                {'state': 'next', 'phase': 'builds', 'completed': 2, 'total': 4}]):
            summary = presentation._release_watch_summary(manifest, 65)
        self.assertIn('/tmp/attempt/build-logs/attempt-002/release-npm-one.log', summary)
        self.assertIn('/tmp/attempt/build-logs/attempt-002/release-npm-two.log', summary)
        self.assertIn('0:01:05', summary)

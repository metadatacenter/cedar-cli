import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from org.metadatacenter import build

class FrontendInputScopeTest(unittest.TestCase):
    def test_includes_nested_sources_and_tooling_but_not_unrelated_backend(self):
        parent = SimpleNamespace(name='cedar-openview', parent_repo=None)
        repo = SimpleNamespace(name='cedar-openview-src', parent_repo=parent)
        plan = SimpleNamespace(tasks=[SimpleNamespace(repo=repo, tasks=[])])
        with patch.object(build.Util, 'cedar_home', '/tmp/cedar'):
            roots = build.frontend_input_roots(plan)
        self.assertEqual(roots, {Path('/tmp/cedar') / name for name in
                                ['cedar-openview', 'cedar-cli', 'cedar-development']})

    def test_changed_frontend_still_fails_but_unrelated_change_does_not(self):
        plan = SimpleNamespace(tasks=[SimpleNamespace(repo=SimpleNamespace(name='cedar-workspace', parent_repo=None), tasks=[])])
        home = Path('/tmp/cedar')
        for name, fails in [('cedar-workspace', True), ('cedar-model-validation-library', False)]:
            with self.subTest(name=name), patch.object(build.Util, 'cedar_home', str(home)), \
                    patch.object(build, 'require_plan_node'), \
                    patch.object(build, 'capture_estate_state', side_effect=[{home/name: b'old'}, {home/name: b'new'}]), \
                    patch.object(build.reactor, 'session_for_plan'), \
                    patch.object(build.reactor, 'runtime_selection', return_value={}), \
                    patch.object(build.plan_executor, 'execute'):
                if fails:
                    with self.assertRaises(SystemExit):
                        build.execute_build(plan, False, False, frontend_only=True)
                else:
                    self.assertEqual(build.execute_build(plan, False, False, frontend_only=True), {})


class DevelopmentInputScopeTest(unittest.TestCase):
    def test_backend_edits_and_commits_do_not_invalidate_frontend_inputs(self):
        import tempfile
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            repo = home / 'cedar-development'
            (repo / 'ops').mkdir(parents=True)
            (repo / 'bin').mkdir()
            profile = repo / 'bin' / 'profile.sh'
            profile.write_text('profile')
            audit = repo / 'ops' / 'audit.py'
            audit.write_text('audit')
            def git(*args):
                subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            git('init')
            git('config', 'user.email', 'test@example.org')
            git('config', 'user.name', 'Test')
            git('add', '.')
            git('commit', '-m', 'Initial')
            baseline = build.capture_build_state(home, True)
            audit.write_text('changed audit')
            self.assertEqual(baseline, build.capture_build_state(home, True))
            git('add', '.')
            git('commit', '-m', 'Backend work')
            self.assertEqual(baseline, build.capture_build_state(home, True))
            profile.write_text('changed profile')
            self.assertNotEqual(baseline, build.capture_build_state(home, True))
            git('add', '.')
            git('commit', '-m', 'Profile work')
            self.assertNotEqual(baseline, build.capture_build_state(home, True))

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from org.metadatacenter import build
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType

class FrontendInputScopeTest(unittest.TestCase):
    def test_includes_nested_sources_and_tooling_but_not_unrelated_backend(self):
        parent = SimpleNamespace(name='cedar-openview', parent_repo=None)
        repo = SimpleNamespace(name='cedar-openview-src', parent_repo=parent)
        plan = SimpleNamespace(tasks=[SimpleNamespace(repo=repo, tasks=[])])
        with patch.object(build.Util, 'cedar_home', '/tmp/cedar'):
            roots = build.build_input_roots(plan)
        self.assertEqual(roots, {Path('/tmp/cedar').resolve() / name for name in
                                ['cedar-openview', 'cedar-cli', 'cedar-development']})

    def test_a_symlinked_home_still_matches_the_resolved_state(self):
        # The captured state is keyed by resolved paths. Unresolved roots matched none of them under
        # a home reached through a symlink, such as /tmp on macOS, and the invariant never fired.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            real = Path(directory).resolve() / 'real'
            (real / 'cedar-workspace').mkdir(parents=True)
            link = Path(directory) / 'link'
            link.symlink_to(real)
            plan = SimpleNamespace(tasks=[SimpleNamespace(
                repo=SimpleNamespace(name='cedar-workspace', parent_repo=None), tasks=[])])
            with patch.object(build.Util, 'cedar_home', str(link)):
                roots = build.build_input_roots(plan)
            self.assertIn(real / 'cedar-workspace', roots)

    def test_changed_frontend_still_fails_but_unrelated_change_does_not(self):
        plan = SimpleNamespace(tasks=[SimpleNamespace(repo=SimpleNamespace(name='cedar-workspace', parent_repo=None), tasks=[])])
        home = Path('/tmp/cedar').resolve()
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
    def test_backend_edits_and_commits_do_not_invalidate_build_inputs(self):
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
            baseline = {flag: build.capture_build_state(home, flag) for flag in (False, True)}
            audit.write_text('changed audit')
            self.assertEqual(baseline, {flag: build.capture_build_state(home, flag) for flag in (False, True)})
            git('add', '.')
            git('commit', '-m', 'Backend work')
            self.assertEqual(baseline, {flag: build.capture_build_state(home, flag) for flag in (False, True)})
            profile.write_text('changed profile')
            self.assertNotEqual(baseline, {flag: build.capture_build_state(home, flag) for flag in (False, True)})
            git('add', '.')
            git('commit', '-m', 'Profile work')
            self.assertNotEqual(baseline, {flag: build.capture_build_state(home, flag) for flag in (False, True)})


class JavaDevelopmentInputScopeTest(unittest.TestCase):
    """A Java build reads the profile in cedar-development and none of its frontend inputs.

    The guard once narrowed cedar-development to the frontend inputs for every build, so a
    `build java` failed because ops/frontend-train.json was committed while it ran.
    """

    def setUp(self):
        import tempfile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.development = self.home / 'cedar-development'
        for relative in ('bin/templates/cedar-profile-native.sh', 'ops/frontend-train.json',
                         'ops/audit.py'):
            path = self.development / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        self.commits = 0
        self.git('init')
        self.commit('Initial')

    def git(self, *args):
        import subprocess
        subprocess.run(['git', '-C', str(self.development), *args], check=True, capture_output=True)

    def commit(self, message, relative=None):
        self.commits += 1
        if relative is not None:
            (self.development / relative).write_text(f'{message} {self.commits}')
        self.git('add', '.')
        self.git('-c', 'user.email=test@example.org', '-c', 'user.name=Test', 'commit', '-m', message)

    @staticmethod
    def plan(*parameters):
        """A plan with one task per parameter set, each building a checkout of its own."""
        plan = Plan('Build')
        for index, task_parameters in enumerate(parameters):
            plan.tasks.append(SimpleNamespace(
                repo=SimpleNamespace(name=f'cedar-repository-{index}', parent_repo=None),
                parameters=task_parameters, tasks=[]))
        return plan

    def test_only_a_frontend_capture_covers_the_frontend_inputs(self):
        baseline = {flag: build.capture_build_state(self.home, flag) for flag in (False, True)}
        self.commit('Frontend inventory work', 'ops/frontend-train.json')
        self.assertEqual(baseline[False], build.capture_build_state(self.home, False))
        self.assertNotEqual(baseline[True], build.capture_build_state(self.home, True))

    def test_the_plan_decides_which_inputs_are_captured(self):
        java = {}
        isolated = {'isolated_frontend_build': True}
        # A server payload builds in place, and frontend_snapshot.prepare does not mark it.
        in_place = {'in_place_frontend_build': True}
        cases = [((java,), False), ((isolated,), True), ((in_place,), True), ((java, isolated), True)]
        for parameters, frontends in cases:
            with self.subTest(parameters=parameters):
                self.assertEqual(frontends, build.builds_frontends(self.plan(*parameters)))

    def run_build(self, plan, changed):
        with patch.object(build.Util, 'cedar_home', str(self.home)), \
                patch.object(build, 'require_plan_node'), \
                patch.object(build.plan_executor, 'execute',
                             side_effect=lambda *_: self.commit('Concurrent work', changed)):
            return build.execute_build(plan, False, False)

    def test_a_frontend_input_committed_during_a_java_build_does_not_fail_it(self):
        self.assertEqual({}, self.run_build(self.plan({}), 'ops/frontend-train.json'))

    def test_a_profile_committed_during_a_java_build_still_fails_it(self):
        with self.assertRaises(SystemExit):
            self.run_build(self.plan({}), 'bin/templates/cedar-profile-native.sh')

    def test_a_frontend_input_committed_during_a_frontend_or_mixed_build_fails_it(self):
        for plan in (self.plan({'in_place_frontend_build': True}),
                     self.plan({}, {'in_place_frontend_build': True})):
            with self.subTest(tasks=len(plan.tasks)), self.assertRaises(SystemExit):
                self.run_build(plan, 'ops/frontend-train.json')

    def test_the_real_planners_classify_java_frontend_and_skipped_builds(self):
        from org.metadatacenter.config.ReposFactory import ReposFactory
        from org.metadatacenter.planner.BuildPlanner import BuildPlanner
        from org.metadatacenter.util.InvocationContext import InvocationContext, use_context
        context = InvocationContext(environment={'CEDAR_HOME': str(self.home),
                                                 'CEDAR_DEV_BUILD_FRONTENDS': 'false'})
        with use_context(context):
            repos = ReposFactory.build_repos()
            context.__dict__['repos'] = repos
            java = Plan('Build java')
            for step in (BuildPlanner.parent, BuildPlanner.libraries, BuildPlanner.project,
                         BuildPlanner.clients):
                step(java)
            self.assertFalse(build.builds_frontends(java))
            mixed = Plan('Build all')
            BuildPlanner.parent(mixed)
            BuildPlanner.frontends(mixed, verification=False)
            self.assertTrue(build.builds_frontends(mixed))
            payload = Plan('Build server frontends')
            BuildPlanner.server_frontends(payload, server_payload=True)
            self.assertTrue(build.builds_frontends(payload))
            # A dist repository has nothing to build, and a frontend the environment disables is
            # skipped, so neither reads a frontend input.
            skipped = Plan('Skipped frontends')
            dist = next(repo for repo in repos.get_list_all() if repo.name == 'cedar-bridging-dist')
            skipped.add_task('Build dist', TaskType.BUILD, [dist], parameters={'force_frontend_build': True})
            skipped.add_task('Build disabled', TaskType.BUILD, [repos.map['cedar-workspace']])
            self.assertFalse(build.builds_frontends(skipped))

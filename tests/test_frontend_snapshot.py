from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import typer
from unittest.mock import patch

from org.metadatacenter import build, frontend_snapshot, reactor
from org.metadatacenter.build_scheduler import dependencies, shell_tasks
from org.metadatacenter.frontend_inventory import ReactorCheck
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.Repos import Repos
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context
from org.metadatacenter.util.Util import Util


class SnapshotPlanTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        context = InvocationContext(environment={'CEDAR_HOME': str(self.home), 'CEDAR_DEV_BUILD_FRONTENDS': 'false'})
        self.repos = context.__dict__['repos'] = Repos()
        stack.enter_context(use_context(context))
        stack.enter_context(patch.object(Util, 'cedar_home', str(self.home)))
        self.checks = {}
        stack.enter_context(patch('org.metadatacenter.frontend_inventory.reactor_checks', return_value=self.checks))

    def repo(self, name, needs=(), *, producer=False):
        repo = Repo(name, RepoType.ANGULAR, [], is_frontend=True,
                    published_package_path='dist' if producer else None,
                    build_command_list=['npm ci', 'npm run build'])
        self.repos.add_repo(repo)
        root = self.home / name
        root.mkdir()
        (root / 'package.json').write_text(json.dumps({'name': name, 'dependencies': dict(needs)}))
        self.checks[name] = ReactorCheck(['npm ci', 'npm run build', 'npm test'], None)
        return repo

    def fixture(self):
        self.repo('cedar-base', producer=True)
        self.repo('cedar-widget', [('model-alias', 'npm:@org.metadatacenter/cedar-base@0.0.1')], producer=True)
        host = self.repo('cedar-host', [('@org.metadatacenter/cedar-widget', '0.0.1')])
        self.repo('cedar-unrelated', producer=True)
        return host

    def test_narrow_build_includes_transitive_sources_even_when_environment_disables_frontends(self):
        host = self.fixture()
        plan = Plan('Build this')
        BuildPlanner.this(plan, Util.get_wd(host))
        frontend_snapshot.prepare(plan)
        tasks = shell_tasks(plan)
        names = [task.repo.name for task in tasks]
        self.assertEqual({'cedar-host', 'cedar-widget', 'cedar-base'}, set(names))
        edges = dependencies(tasks)
        self.assertIn(names.index('cedar-widget'), edges[names.index('cedar-host')])
        self.assertIn(names.index('cedar-base'), edges[names.index('cedar-widget')])
        self.assertTrue(plan.frontend_snapshot)
        for task in tasks:
            self.assertTrue(task.parameters['isolated_frontend_build'])
            if task.repo.name != 'cedar-host':
                self.assertTrue(task.parameters['reuse_passing_result'])
        frontend_snapshot.prepare(plan)
        self.assertEqual(3, len(shell_tasks(plan)), 'Repeated preparation must not duplicate producers')

    def test_nested_manifest_cannot_hide_a_registry_dependency(self):
        host = self.fixture()
        nested = self.home / host.name / 'visual'
        nested.mkdir()
        (nested / 'package.json').write_text(json.dumps({'devDependencies': {'cedar-unrelated': '0.0.1'}}))
        plan = Plan('Build this')
        BuildPlanner.this(plan, Util.get_wd(host))
        frontend_snapshot.prepare(plan)
        self.assertEqual(4, len(shell_tasks(plan)))

    def test_unversioned_scoped_alias_still_requires_its_local_producer_first(self):
        self.repo('cedar-widget', producer=True)
        host = self.repo('cedar-host', [('alias', 'npm:@org.metadatacenter/cedar-widget')])
        plan = Plan('Build this')
        BuildPlanner.this(plan, Util.get_wd(host))
        frontend_snapshot.prepare(plan)
        tasks = shell_tasks(plan)
        names = [t.repo.name for t in tasks]
        self.assertEqual({'cedar-widget', 'cedar-host'}, set(names))
        self.assertIn(names.index('cedar-widget'), dependencies(tasks)[names.index('cedar-host')])

    def test_missing_or_unregistered_producer_fails_instead_of_using_a_pin(self):
        for absent in [False, True]:
            with self.subTest(absent=absent):
                if not absent:
                    host = self.repo('cedar-host', [('cedar-missing', '1.0.0')])
                else:
                    missing = self.repo('cedar-missing', producer=True)
                    (self.home / missing.name / 'package.json').unlink()
                plan = Plan('Build this')
                BuildPlanner.this(plan, Util.get_wd(host))
                with self.assertRaises(reactor.ReactorError):
                    frontend_snapshot.prepare(plan)

    def test_explicit_server_payload_is_not_expanded_or_resolved_from_local_artifacts(self):
        host = self.fixture()
        host.server_build_command_list = ['npm ci', 'npm run build:server']
        plan = Plan('Server payload')
        plan.add_task('Build server', TaskType.BUILD, [host],
                      parameters={'force_frontend_build': True, 'server_frontend_payload': True})
        frontend_snapshot.prepare(plan)
        self.assertFalse(getattr(plan, 'frontend_snapshot', False))
        self.assertEqual(1, len(shell_tasks(plan)))
        with patch.object(reactor, '_available', side_effect=AssertionError('must not inspect local snapshots')):
            with reactor.session_for_plan(self.home, plan):
                self.assertIsNone(reactor._build(self.home))

    def test_full_plan_still_rejects_an_unregistered_dependency(self):
        producer = self.repo('cedar-widget', [('cedar-missing', '1.0.0')], producer=True)
        plan = Plan('All producers')
        BuildPlanner.this(plan, Util.get_wd(producer))
        with self.assertRaisesRegex(reactor.ReactorError, 'no local package producer'):
            frontend_snapshot.prepare(plan)


class SnapshotResolutionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.consumer = self.home / 'isolated'
        self.consumer.mkdir()
        self.manifest = self.consumer / 'package.json'
        self.manifest.write_text(json.dumps({'name': 'host', 'dependencies': {'cedar-widget': '1.0.0'}}))

    def artifact(self, data):
        digest = hashlib.sha256(data).hexdigest()
        path = self.home / '.reactor/artifacts' / (digest + '.tgz')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self, *names):
        return SimpleNamespace(tasks=[SimpleNamespace(tasks=[], repo=SimpleNamespace(
            name=name, published_package_path='dist'), parameters={'isolated_frontend_build': True})
            for name in names])

    def test_old_refs_never_satisfy_an_omitted_or_failed_current_producer(self):
        old = self.artifact(b'old')
        refs = self.home / '.reactor/refs'
        refs.mkdir()
        (refs / 'cedar-widget.json').write_text(json.dumps({'sha256': old.stem}))
        for names in [('cedar-other',), ('cedar-widget',)]:
            with reactor.session_for_plan(self.home, self.plan(*names)):
                self.assertNotIn('cedar-widget', reactor._state(self.home)[0])
                with self.assertRaises(reactor.ReactorError):
                    reactor.resolve(self.consumer, self.home)
        self.assertEqual('1.0.0', json.loads(self.manifest.read_text())['dependencies']['cedar-widget'])

    def test_only_this_builds_verified_artifact_can_replace_the_pin(self):
        package = self.artifact(b'current working-tree bytes')
        with reactor.session_for_plan(self.home, self.plan('cedar-widget')):
            state = reactor._build(self.home)
            state.available['cedar-widget'] = package
            state.pending.clear()
            reactor.resolve(self.consumer, self.home)
            self.assertEqual('file:' + str(package), json.loads(self.manifest.read_text())['dependencies']['cedar-widget'])

    def test_changed_artifact_bytes_are_refused(self):
        package = self.artifact(b'current')
        package.write_bytes(b'corrupted')
        with reactor.session_for_plan(self.home, self.plan('cedar-widget')):
            state = reactor._build(self.home)
            state.available['cedar-widget'] = package
            state.pending.clear()
            with self.assertRaisesRegex(reactor.ReactorError, 'digest'):
                reactor.resolve(self.consumer, self.home)

    def test_unversioned_scoped_alias_cannot_resolve_from_the_registry(self):
        self.manifest.write_text(json.dumps({'dependencies': {
            'alias': 'npm:@org.metadatacenter/cedar-widget'}}))
        with reactor.session_for_plan(self.home, self.plan('cedar-widget')):
            with self.assertRaisesRegex(reactor.ReactorError, 'has not succeeded'):
                reactor.resolve(self.consumer, self.home)
            state = reactor._build(self.home)
            package = self.artifact(b'current source')
            state.available['cedar-widget'] = package
            state.pending.clear()
            reactor.resolve(self.consumer, self.home)
            self.assertEqual('file:' + str(package), json.loads(self.manifest.read_text())['dependencies']['alias'])

    def test_invalid_manifests_cannot_bypass_dependency_resolution(self):
        for contents in ['{', '[]', '{"dependencies": []}']:
            with self.subTest(contents=contents):
                self.manifest.write_text(contents)
                with reactor.session_for_plan(self.home, self.plan('cedar-widget')):
                    with self.assertRaises(reactor.ReactorError):
                        reactor.resolve(self.consumer, self.home)

    def test_dry_run_and_execution_both_prepare_snapshot_dependencies(self):
        for dry_run in [False, True]:
            with patch('org.metadatacenter.frontend_snapshot.prepare', side_effect=reactor.ReactorError('missing source')):
                with self.assertRaises(typer.Exit):
                    build.execute_build(Plan('local'), dry_run, False)

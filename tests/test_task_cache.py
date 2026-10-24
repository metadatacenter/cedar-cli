import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from org.metadatacenter import task_cache, reactor_tasks


class TaskCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / 'copy'
        self.root.mkdir()
        (self.root / 'package-lock.json').write_text('{"version":1}')
        (self.root / 'dist').mkdir()
        (self.root / 'dist/app.js').write_text('current bundle')
        self.config = {'root': str(self.root), 'directory': str(self.home / 'cache'),
                       'base': {'node': '24', 'dependencies': 'current'},
                       'locks': ['package-lock.json'],
                       'uncacheable': str(self.root / 'unstable'),
                       'tasks': {'build': {'source': {'src': 'a'}, 'outputs': ['dist']},
                                 'browser': {'source': {'tests': 'b'}, 'outputs': [], 'reads': ['dist']}}}

    def test_restores_exact_outputs_and_records_evidence(self):
        saved = task_cache.save(self.config, 'build', log='compiled')
        (self.root / 'dist/app.js').write_text('wrong')
        (self.root / 'dist/extra.js').write_text('stale')
        hit = task_cache.reuse(self.config, 'build')
        self.assertEqual(saved['key'], hit['key'])
        self.assertEqual('current bundle', (self.root / 'dist/app.js').read_text())
        self.assertFalse((self.root / 'dist/extra.js').exists())
        self.assertEqual('compiled', (self.home / 'cache' / saved['key'] / 'output.log').read_text())

    def test_bundle_tests_and_environment_each_invalidate_browser_results(self):
        task_cache.save(self.config, 'browser', {'CED_WEBKIT': '1'})
        self.assertIsNotNone(task_cache.reuse(self.config, 'browser', {'CED_WEBKIT': '1'}))
        self.assertIsNone(task_cache.reuse(self.config, 'browser', {'CED_WEBKIT': '0'}))
        (self.root / 'dist/app.js').write_text('new bundle')
        self.assertIsNone(task_cache.reuse(self.config, 'browser', {'CED_WEBKIT': '1'}))
        (self.root / 'dist/app.js').write_text('current bundle')
        self.config['tasks']['browser']['source']['tests'] = 'changed test'
        self.assertIsNone(task_cache.reuse(self.config, 'browser', {'CED_WEBKIT': '1'}))

    def test_resolved_external_lock_changes_invalidate(self):
        task_cache.save(self.config, 'build')
        (self.root / 'package-lock.json').write_text('{"version":2}')
        self.assertIsNone(task_cache.reuse(self.config, 'build'))

    def test_local_tarball_paths_are_relocatable_but_bytes_are_not_interchangeable(self):
        path = self.root / 'package-lock.json'
        path.write_text(json.dumps({'resolved': 'file:../../scratch/' + 'a' * 64 + '.tgz'}))
        task_cache.save(self.config, 'build')
        path.write_text(json.dumps({'resolved': 'file:/another/location/' + 'a' * 64 + '.tgz'}))
        self.assertIsNotNone(task_cache.reuse(self.config, 'build'))
        path.write_text(json.dumps({'resolved': 'file:/another/location/' + 'b' * 64 + '.tgz'}))
        self.assertIsNone(task_cache.reuse(self.config, 'build'))

    def test_corrupt_or_missing_archive_is_a_miss_without_destroying_current_outputs(self):
        saved = task_cache.save(self.config, 'build')
        path = self.home / 'cache' / saved['key'] / 'outputs.tar'
        path.write_bytes(b'corrupt')
        self.assertIsNone(task_cache.reuse(self.config, 'build'))
        self.assertEqual('current bundle', (self.root / 'dist/app.js').read_text())
        path.unlink()
        self.assertIsNone(task_cache.reuse(self.config, 'build'))
        task_cache.save(self.config, 'build')
        self.assertIsNotNone(task_cache.reuse(self.config, 'build'))

    def test_corrupt_result_is_a_miss(self):
        saved = task_cache.save(self.config, 'build')
        path = self.home / 'cache' / saved['key'] / 'result.json'
        for value in ['{', 'null', '[]', '{}']:
            path.write_text(value)
            self.assertIsNone(task_cache.reuse(self.config, 'build'))

    @unittest.skipUnless(shutil.which('node'), 'requires node')
    def test_javascript_gate_restores_outputs_and_rejects_retry_success(self):
        module = Path(reactor_tasks.__file__).resolve().parents[2] / 'scripts/javascript/reactor-stage-cache.mjs'
        self.config.update(python=sys.executable, engine=str(Path(task_cache.__file__).resolve()))
        path = self.home / 'config.json'
        path.write_text(json.dumps(self.config))
        code = '''
import assert from 'node:assert/strict';
import { writeFileSync, readFileSync, existsSync } from 'node:fs';
const { stageCache } = await import(process.env.CACHE_MODULE);
const cache = stageCache();
const stage = { name: 'build', script: 'compile' };
assert.equal(cache.reuse(stage), false);
cache.observe(stage, 'built successfully');
cache.save(stage);
writeFileSync('dist/app.js', 'stale');
assert.equal(cache.reuse(stage), true);
assert.equal(readFileSync('dist/app.js', 'utf8'), 'current bundle');
const browser = { name: 'browser', script: 'browser-test' };
cache.observe(browser, '1 flaky\\n100 passed');
cache.save(browser);
assert.equal(cache.reuse(browser), false);
assert.ok(existsSync('unstable'));
'''
        result = subprocess.run(['node', '--input-type=module', '-e', code], cwd=self.root,
                                env={**os.environ, 'CACHE_MODULE': module.as_uri(),
                                     reactor_tasks.CONFIG: str(path)}, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_missing_outputs_cannot_be_recorded_or_used_as_inputs(self):
        (self.root / 'dist/app.js').unlink()
        (self.root / 'dist').rmdir()
        self.assertIsNone(task_cache.save(self.config, 'build'))
        self.assertIsNone(task_cache.save(self.config, 'browser'))

    def test_a_flaky_success_is_not_cached_and_marks_the_repository(self):
        self.assertIsNone(task_cache.save(self.config, 'browser', log='1 flaky\n100 passed'))
        self.assertTrue(Path(self.config['uncacheable']).exists())
        self.assertIsNone(task_cache.reuse(self.config, 'browser'))

    def test_completed_stages_remain_private_until_the_source_guard_allows_publication(self):
        self.config['writeDirectory'] = str(self.root / '.pending')
        saved = task_cache.save(self.config, 'build')
        self.assertFalse((self.home / 'cache' / saved['key']).exists())
        another = {k: v for k, v in self.config.items() if k != 'writeDirectory'}
        self.assertIsNone(task_cache.reuse(another, 'build'))
        self.assertIsNotNone(task_cache.reuse(self.config, 'build'))
        task_cache.publish(self.config)
        self.assertIsNotNone(task_cache.reuse(another, 'build'))

    def test_equivalent_gate_command_reuses_only_the_identical_script_and_environment(self):
        task_cache.save(self.config, 'build')
        self.config['tasks']['package'] = {**self.config['tasks']['build'],
                                          'equivalentCommand': 'build', 'script': 'build-script'}
        self.assertIsNotNone(task_cache.reuse(self.config, 'package',
                                             {'script': 'build-script', 'environment': {}}))
        self.assertIsNone(task_cache.reuse(self.config, 'package',
                                          {'script': 'other-script', 'environment': {}}))
        self.assertIsNone(task_cache.reuse(self.config, 'package',
                                          {'script': 'build-script', 'environment': {'MODE': 'other'}}))

    def test_output_paths_cannot_escape_the_isolated_copy(self):
        for path in ['../outside', '/absolute', '.']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                task_cache.safe_path(self.root, path)


class InputBoundariesTest(unittest.TestCase):
    def test_no_reuse_clears_inherited_task_cache(self):
        from types import SimpleNamespace
        environment = {reactor_tasks.CONFIG: 'inherited'}
        reactor_tasks.configure(environment, SimpleNamespace(name='cedar-workspace'),
                                None, '/unused', '/unused', '/unused')
        self.assertEqual({}, environment)
    def test_unit_edit_keeps_browser_inputs_but_source_and_shared_fixtures_remain(self):
        include = reactor_tasks.includes
        self.assertFalse(include('src/app/menu.spec.ts', 'browser', 'cedar-workspace'))
        self.assertTrue(include('src/app/menu.spec.ts', 'unit', 'cedar-workspace'))
        self.assertTrue(include('src/app/menu.ts', 'browser', 'cedar-workspace'))
        self.assertTrue(include('visual/fixtures/example.json', 'unit', 'cedar-embeddable-editor'))
        self.assertTrue(include('new-build-tool.mjs', 'browser', 'cedar-workspace'))

    def test_browser_edit_does_not_invalidate_unit_or_build(self):
        for repository in ['cedar-workspace', 'cedar-embeddable-editor', 'cedar-embeddable-designer']:
            for kind in ['unit', 'build', 'package']:
                self.assertFalse(reactor_tasks.includes('browser/tests/menu.spec.ts', kind, repository))
            self.assertTrue(reactor_tasks.includes('browser/tests/menu.spec.ts', 'browser', repository))

    def test_shipped_readme_and_ced_build_provenance_keep_their_inputs(self):
        self.assertTrue(reactor_tasks.includes('README.md', 'package', 'cedar-embeddable-designer'))
        self.assertTrue(reactor_tasks.includes('src/menu.spec.ts', 'package', 'cedar-embeddable-designer'))
        self.assertFalse(reactor_tasks.includes('src/menu.spec.ts', 'build', 'cedar-workspace'))


if __name__ == '__main__':
    unittest.main()

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from org.metadatacenter import reactor_stages


class StageConfigurationTest(unittest.TestCase):
    def test_no_reuse_clears_inherited_permission_and_producers_are_never_cached(self):
        environment = {'CEDAR_STAGE_CACHE_MODULE': 'old', 'CEDAR_STAGE_CACHE': 'old'}
        repo = SimpleNamespace(name='cedar-embeddable-editor')
        reactor_stages.configure(environment, repo, None, '/tmp/cedar')
        self.assertEqual({}, environment)
        reactor_stages.configure(environment, repo, ('a' * 64, {}, None), '/tmp/cedar')
        self.assertTrue(Path(environment['CEDAR_STAGE_CACHE_MODULE'][7:]).is_file())
        for stages in reactor_stages.STAGES.values():
            self.assertFalse(set(stages) & {'build', 'fixtures', 'dist', 'package'})


@unittest.skipUnless(shutil.which('node'), 'requires node')
class GateResumeTest(unittest.TestCase):
    def test_cache_protocol_without_any_sibling_checkout(self):
        module = Path(reactor_stages.__file__).resolve().parents[2] / 'scripts/javascript/reactor-stage-cache.mjs'
        with tempfile.TemporaryDirectory() as directory:
            code = """
import assert from 'node:assert/strict';
import { readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const { stageCache } = await import(process.env.CACHE_MODULE);
const config = { directory: process.env.CACHE_ROOT, fingerprint: 'a'.repeat(64), stages: ['unit'] };
const create = () => stageCache({ CEDAR_STAGE_CACHE: JSON.stringify(config) });
const stage = { name: 'unit', script: 'test', env: { CED_WEBKIT: '1' } };
const cache = create();
assert.equal(cache.reuse(stage), false);
cache.save(stage);
assert.equal(cache.reuse(stage), true);
assert.equal(cache.reuse({ ...stage, script: 'different-test' }), false);
assert.equal(cache.reuse({ ...stage, env: { CED_WEBKIT: '0' } }), false);
cache.save({ name: 'dist', script: 'build' });
assert.equal(readdirSync(config.directory).length, 1);
config.fingerprint = 'b'.repeat(64);
assert.equal(create().reuse(stage), false);
for (const value of ['{', '{}', 'null', '{"exitCode":1}']) {
  writeFileSync(join(config.directory, readdirSync(config.directory)[0]), value);
  assert.equal(cache.reuse(stage), false);
}
for (const value of [undefined, '', '{}', 'null', '[]', '{"directory":42}']) {
  const disabled = stageCache({ CEDAR_STAGE_CACHE: value });
  assert.equal(disabled.reuse(stage), false);
  disabled.save(stage);
}
"""
            result = subprocess.run(['node', '--input-type=module', '-e', code],
                                    env={**os.environ, 'CACHE_MODULE': module.as_uri(), 'CACHE_ROOT': directory},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_failure_repeats_only_unpassed_checks_but_always_recreates_package(self):
        gate = Path(__file__).resolve().parents[2] / 'cedar-embeddable-editor/scripts/test-gate.mjs'
        if not gate.is_file():
            self.skipTest('requires CEE sibling checkout')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            npm = root / 'npm'
            npm.write_text('#!' + shutil.which('node') + '\n' + """
const fs = require('node:fs');
fs.appendFileSync(process.env.STAGE_LOG, process.argv[3] + '\\n');
if (process.env.CEDAR_STAGE_CACHE) process.exit(99); // No child may certify its parent.
process.exit(process.argv[3] === process.env.FAIL_STAGE ? 7 : 0);
""")
            npm.chmod(0o755)
            environment = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                           'CEDAR_TEST_WORKERS': '1', 'STAGE_LOG': str(root / 'events')}
            environment.pop('CEDAR_WORKER_POOL', None)
            reactor_stages.configure(environment, SimpleNamespace(name='cedar-embeddable-editor'),
                                     ('a' * 64, {}, None), root)

            def run(fail=''):
                (root / 'events').write_text('')
                result = subprocess.run(['node', str(gate), 'prebuilt'], env={**environment, 'FAIL_STAGE': fail},
                                        cwd=root, capture_output=True, text=True, timeout=30)
                return result, (root / 'events').read_text().splitlines()

            first, scripts = run('test:visual:prebuilt')
            self.assertNotEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn('test:unit:ci', scripts, first.stdout + first.stderr)
            again, scripts = run('test:visual:prebuilt')
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(['test:visual:prebuilt'], scripts)
            passed, scripts = run()
            self.assertEqual(0, passed.returncode, passed.stdout + passed.stderr)
            self.assertEqual(['test:visual:prebuilt', 'package:npm:prebuilt'], scripts)
            cached, scripts = run()
            self.assertEqual(0, cached.returncode, cached.stdout + cached.stderr)
            self.assertEqual(['package:npm:prebuilt'], scripts)
            # Truncated evidence is never a pass; changed source/dependencies/tooling key rechecks all.
            for record in (root / '.reactor/stage-results').glob('*.json'):
                record.write_text('{')
            self.assertIn('test:unit:ci', run()[1])
            config = json.loads(environment['CEDAR_STAGE_CACHE'])
            config['fingerprint'] = 'b' * 64
            environment['CEDAR_STAGE_CACHE'] = json.dumps(config)
            self.assertIn('test:unit:ci', run()[1])

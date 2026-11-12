import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from org.metadatacenter.design_token_runtime import inspect_runtime
from org.metadatacenter.reactor_evidence import source_identity


class TokenRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'cedar-design-tokens'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        (self.repo / 'recipe.scss').write_text('a { color: red; }')
        subprocess.run(['git', '-C', str(self.repo), 'add', 'recipe.scss'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=t@t',
                        'commit', '-qm', 'Initial source'], check=True)
        self.store = self.home / '.reactor'
        (self.store / 'artifacts').mkdir(parents=True)
        (self.store / 'builds').mkdir()
        self.artifact = b'immutable test package'
        self.digest = hashlib.sha256(self.artifact).hexdigest()
        (self.store / 'artifacts' / (self.digest + '.tgz')).write_bytes(self.artifact)
        self.record = {'schemaVersion': 1, 'packages': {'cedar-design-tokens': self.digest},
                       'repositories': {'cedar-design-tokens': {'source': source_identity(self.repo), 'dependencies': []}}}
        self.select()

    def select(self):
        content = (json.dumps(self.record, sort_keys=True, indent=2) + '\n').encode()
        self.build = hashlib.sha256(content).hexdigest()
        (self.store / 'builds' / (self.build + '.json')).write_bytes(content)
        (self.store / 'runtime.json').write_text(json.dumps({'build': self.build, 'packages': self.record['packages']}))

    def test_current_source_and_artifact_pass_without_writes(self):
        before = {p: p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        self.assertEqual('current', inspect_runtime(self.home)['state'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.store.rglob('*') if p.is_file()})

    def test_uncommitted_edits_and_untracked_source_are_stale(self):
        (self.repo / 'recipe.scss').write_text('a { color: blue; }')
        self.assertEqual('stale', inspect_runtime(self.home)['state'])
        (self.repo / 'recipe.scss').write_text('a { color: red; }')
        (self.repo / 'new.scss').write_text('new input')
        self.assertEqual('stale', inspect_runtime(self.home)['state'])

    def test_build_of_dirty_sources_is_current_when_content_still_matches(self):
        (self.repo / 'recipe.scss').write_text('a { color: blue; }')
        self.record['repositories']['cedar-design-tokens']['source'] = source_identity(self.repo)
        self.select()
        self.assertEqual('current', inspect_runtime(self.home)['state'])

    def test_tampered_artifact_and_build_record_fail(self):
        artifact = self.store / 'artifacts' / (self.digest + '.tgz')
        artifact.write_bytes(b'changed')
        self.assertEqual('invalid', inspect_runtime(self.home)['state'])
        artifact.write_bytes(self.artifact)
        (self.store / 'builds' / (self.build + '.json')).write_text('{}')
        self.assertEqual('invalid', inspect_runtime(self.home)['state'])

    def test_missing_selection_and_missing_source_evidence_fail(self):
        (self.store / 'runtime.json').unlink()
        self.assertEqual('missing', inspect_runtime(self.home)['state'])
        self.select()
        self.assertEqual('invalid', inspect_runtime(self.home, ['cedar-workspace'])['state'])
        self.record['repositories'] = {}
        self.select()
        self.assertEqual('invalid', inspect_runtime(self.home)['state'])

    def test_inconsistent_dependency_edges_fail(self):
        self.record['repositories']['cedar-design-tokens']['dependencies'] = [
            {'dependency': '@org.metadatacenter/cedar-design-tokens', 'sha256': '0' * 64}]
        self.select()
        self.assertEqual('invalid', inspect_runtime(self.home)['state'])

    def test_untrusted_record_paths_and_inconsistent_edges_fail(self):
        self.record['repositories']['../outside'] = {'dependencies': []}
        self.select()
        self.assertEqual('invalid', inspect_runtime(self.home)['state'])

    def test_new_consumer_absent_from_old_build_is_reported(self):
        consumer = self.home / 'cedar-workspace'
        consumer.mkdir()
        (consumer / 'package.json').write_text(json.dumps({'devDependencies': {
            '@org.metadatacenter/cedar-design-tokens': '1.0.0'}}))
        report = inspect_runtime(self.home)
        self.assertEqual('invalid', report['state'])
        self.assertIn('cedar-workspace', report['errors'][0])


if __name__ == '__main__':
    unittest.main()

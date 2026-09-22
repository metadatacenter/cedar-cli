import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import hashlib
import io
import tarfile
from types import SimpleNamespace
from unittest.mock import patch

from org.metadatacenter import reactor, reactor_evidence as evidence


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'consumer'
        self.repo.mkdir()
        (self.repo / 'package.json').write_text('{"dependencies":{"producer":"1.0.0"}}')
        for args in [('init', '-q'), ('add', 'package.json'),
                     ('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'initial')]:
            subprocess.run(['git', '-C', str(self.repo), *args], check=True)

    def test_identity_includes_dirty_and_new_source_bytes(self):
        original = evidence.source_identity(self.repo)
        added = self.repo / 'new.ts'
        added.write_text('first')
        first = evidence.source_identity(self.repo)
        added.write_text('second')
        self.assertNotEqual(first['sourceSha256'], evidence.source_identity(self.repo)['sourceSha256'])
        self.assertTrue(first['dirty'])
        self.assertNotEqual(original['sourceSha256'], first['sourceSha256'])

    def test_records_resolved_graph_without_modifying_source_and_detects_later_edits(self):
        copy = self.home / 'copy'
        copy.mkdir()
        spec = 'file:/artifacts/' + 'a' * 64 + '.tgz'
        (copy / 'package.json').write_text(json.dumps({'dependencies': {'producer': spec}}))
        (copy / 'package-lock.json').write_text('{"lockfileVersion":3}')
        with evidence.session():
            identity = evidence.begin(self.repo)
            evidence.record(self.repo, copy, identity, ['npm install', 'npm run build'])
            digest = evidence.finish(self.home, {'producer': 'a' * 64})
            record = json.loads((self.home / '.reactor/builds' / (digest + '.json')).read_text())
            self.assertEqual('a' * 64, record['repositories']['consumer']['dependencies'][0]['sha256'])
            self.assertEqual({'lockfileVersion': 3}, record['repositories']['consumer']['resolvedManifests']['package-lock.json'])
            self.assertEqual('1.0.0', json.loads((self.repo / 'package.json').read_text())['dependencies']['producer'])
            (self.repo / 'new.ts').write_text('later edit')
            with self.assertRaisesRegex(reactor.ReactorError, 'changed during reactor'):
                evidence.finish(self.home, {})

    def test_runtime_links_to_evidence(self):
        reactor.activate_runtime(self.home, evidence.Selection({'producer': 'a' * 64}, 'b' * 64))
        self.assertEqual('b' * 64, json.loads((self.home / '.reactor/runtime.json').read_text())['build'])

    def test_change_before_a_later_task_is_rejected(self):
        plan = SimpleNamespace(tasks=[SimpleNamespace(tasks=[], repo=object(),
                               parameters={'isolated_frontend_build': True})])
        with patch('org.metadatacenter.util.Util.Util.get_wd', return_value=str(self.repo)):
            with evidence.session(plan):
                (self.repo / 'new.ts').write_text('changed after reactor started')
                with self.assertRaisesRegex(reactor.ReactorError, 'changed before'):
                    evidence.begin(self.repo)

    def test_designer_checks_receive_verified_bundles_from_the_selected_artifacts(self):
        artifacts = self.home / '.reactor/artifacts'
        refs = self.home / '.reactor/refs'
        artifacts.mkdir(parents=True)
        refs.mkdir()
        for name in ('cedar-embeddable-editor', 'cedar-embeddable-term-picker'):
            content = name.encode()
            packed = io.BytesIO()
            with tarfile.open(fileobj=packed, mode='w:gz') as archive:
                entry = tarfile.TarInfo('package/' + name + '.js')
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
            digest = hashlib.sha256(packed.getvalue()).hexdigest()
            (artifacts / (digest + '.tgz')).write_bytes(packed.getvalue())
            (refs / (name + '.json')).write_text(json.dumps({'sha256': digest}))
        repo = SimpleNamespace(name='cedar-embeddable-designer')
        environment = {}
        with reactor.session(self.home):
            inputs = reactor.prepare_checks(repo, self.repo, self.home, environment, ['npm run test:ci'])
        self.assertEqual(2, len(inputs))
        self.assertEqual(b'cedar-embeddable-editor', Path(environment['CEF_BUNDLE']).read_bytes())
        self.assertEqual(b'cedar-embeddable-term-picker', Path(environment['PICKER_BUNDLE']).read_bytes())
        with reactor.session(self.home, ['cedar-embeddable-editor']):
            with self.assertRaisesRegex(reactor.ReactorError, 'require a completed'):
                reactor.prepare_checks(repo, self.repo, self.home, environment, ['npm run test:ci'])

import hashlib
import base64
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
from org.metadatacenter.release_readiness_report import ReadinessReport
from org.metadatacenter.release_support.errors import ReleaseError


class ReadinessReportTest(unittest.TestCase):
    def test_package_integrity_and_identity_are_required(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            data = b'{"name":"cedar-model-typescript-library","version":"1.0.15"}'
            entry = tarfile.TarInfo('package/package.json'); entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        data = output.getvalue()
        integrity = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
        http = Mock()
        http.read_json.return_value = ({'dist': {'tarball':'https://example.test/package.tgz', 'integrity':integrity}}, b'')
        http.read.return_value = data
        report = ReadinessReport('/unused', http=http)
        self.assertIn('integrity', report.model('1.0.15'))
        with self.assertRaisesRegex(ReleaseError, 'identity'):
            report.model('1.0.16')
        http.read.return_value = data + b'corrupt'
        with self.assertRaisesRegex(ReleaseError, 'integrity'):
            report.model('1.0.15')

    def test_cee_declared_model_must_match_bundle_and_requested_model(self):
        report = ReadinessReport('/unused')
        files = {'CHANGELOG.md': b'## [2.0.18] - 2026-09-27\nUses cedar-model-typescript-library@1.0.15\n',
                 'editor.js': b'{"cedar-model-typescript-library":"1.0.15"}'}
        with patch.object(report, 'package', return_value=files):
            self.assertIn('embeds model pin 1.0.15', report.cee('2.0.18', '1.0.15'))
            with self.assertRaisesRegex(ReleaseError, 'expected'):
                report.cee('2.0.18', '1.0.16')
            files['editor.js'] = b'{"cedar-model-typescript-library":"1.0.14"}'
            with self.assertRaisesRegex(ReleaseError, 'provenance'):
                report.cee('2.0.18', '1.0.15')

    def test_failures_accumulate_and_unknowns_are_not_passes(self):
        report = ReadinessReport('/unused')
        module = 'org.metadatacenter.release_readiness_report'
        with patch(module + '.readiness_findings', return_value=['bad surface']), \
             patch.object(report, 'pins', side_effect=ReleaseError('bad lock')), \
             patch.object(report, 'sources', return_value={'repositories': {'repo':'a'*40}}), \
             patch(module + '.preflight._source_ci_preflight', side_effect=ValueError('CI pending')), \
             patch(module + '.preflight._smoke_gate_preflight', side_effect=ValueError('stale smoke')), \
             patch(module + '.survey._open_work', return_value=[]), \
             patch(module + '.survey._source_alignment', return_value=[]):
            rows = report.run(packaging=False)
        indexed = {row['check']: row for row in rows}
        for name in ['Workspace', 'Consumer pins', 'Exact-source CI', 'Whole-stack smoke']:
            self.assertEqual('fail', indexed[name]['status'])
            self.assertTrue(indexed[name]['next'])
        self.assertEqual('not checked', indexed['Public model']['status'])
        self.assertEqual('not checked', indexed['Train artifacts/equivalence']['status'])
        self.assertEqual('not checked', indexed['Packaging']['status'])

    def test_consumer_manifest_lock_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'repo').mkdir()
            (root/'repo/package.json').write_text(json.dumps({'dependencies': {'cedar-embeddable-editor':'2.0.18'}}))
            lock = {'packages': {'': {'dependencies': {'cedar-embeddable-editor':'2.0.17'}},
                                 'node_modules/cedar-embeddable-editor': {'version':'2.0.17'}}}
            (root/'repo/package-lock.json').write_text(json.dumps(lock))
            config = {'additionalCeeConsumers': [{'repository':'repo','manifest':'package.json','lock':'package-lock.json'}]}
            with patch('org.metadatacenter.release_readiness_report._configuration', return_value=(config, {})):
                with self.assertRaisesRegex(ReleaseError, 'inconsistent'):
                    ReadinessReport(root).pins()

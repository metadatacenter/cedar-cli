import unittest
from org.metadatacenter.version_lifecycle import analyze


def row(key, previous=None, status='bibo:published', version='1.0.0', latest=False, draft=False, published=False):
    node = {'@id': key, 'resourceType': 'template', 'pav:version': version, 'bibo:status': status,
            'isLatestVersion': latest, 'isLatestDraftVersion': draft, 'isLatestPublishedVersion': published}
    if previous:
        node['pav:previousVersion'] = previous
    return {'node': node, 'previous': [previous] if previous else []}


class VersionLifecycleTests(unittest.TestCase):
    def test_legacy_flags_are_repairable_without_guessing_history(self):
        rows = [row('a'), row('b', 'a', 'bibo:draft', '1.0.1')]
        findings, expected = analyze(rows)
        self.assertEqual({'incorrect-latest-flags'}, {f['issue'] for f in findings})
        self.assertTrue(expected['a']['isLatestPublishedVersion'])
        self.assertFalse(expected['a']['isLatestVersion'])
        self.assertTrue(expected['b']['isLatestVersion'])

    def test_broken_and_branched_series_are_not_repaired(self):
        rows = [row('a'), row('b', 'a', version='2.0.0'), row('c', 'a', version='3.0.0'), row('d', 'missing')]
        findings, expected = analyze(rows)
        self.assertEqual({}, expected)
        self.assertIn('branched-history', {f['issue'] for f in findings})
        self.assertIn('missing-predecessor', {f['issue'] for f in findings})

    def test_cycles_terminate_and_are_reported(self):
        findings, expected = analyze([row('a', 'b'), row('b', 'a', version='2.0.0')])
        self.assertEqual({}, expected)
        self.assertIn('non-increasing-version', {f['issue'] for f in findings})

    def test_relationship_disagreement_and_multiple_drafts_are_reported(self):
        rows = [row('a', status='bibo:draft'), row('b', 'a', 'bibo:draft', '1.0.1')]
        rows[1]['previous'] = []
        findings, expected = analyze(rows)
        self.assertEqual({}, expected)
        self.assertTrue({'multiple-drafts', 'draft-has-successor', 'property-relationship-disagreement'} <= {f['issue'] for f in findings})

    def test_healthy_series_and_numeric_ordering(self):
        rows = [row('a', version='2.0.0'), row('b', 'a', version='10.0.0', latest=True, published=True)]
        self.assertEqual([], analyze(rows)[0])

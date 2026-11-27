import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from typer.testing import CliRunner

from org.metadatacenter import test_processes, ui_review_board as board
from org.metadatacenter.ui_review_board import read_json


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)


def finding(id_, state, decision=None, calibrated=True, kind='defect', title='1. Save overlaps the name',
            target='Save', observed='Save overlaps the name.'):
    verification = {'id': id_, 'decision': decision, 'reason': 'Seen in the capture.', 'evidence': []} if decision else None
    captions = [{'file': state + '.png', 'caption': 'The card, where Save covers the name.'}] if id_ == 'A-F1' else []
    item = {'id': id_, 'title': title, 'target': target, 'category': 'alignment', 'evidenceCaptions': captions,
            'observed': observed, 'expected': 'Keep them apart.', 'impact': 'The name is hidden.',
            'kind': kind, 'severity': 'medium', 'stateIds': [state], 'steps': ['Open the state.'],
            'evidence': [state + '.png', 'missing.png', 'notes.json'], 'verification': verification, 'calibrated': calibrated}
    return {**item, 'occurrences': [item]}


def fixture(root):
    reports = root / 'reports'
    run = reports / 'ui-review-coordinator/designer-run'
    for state in ('type-text', 'term-picker-open-1440'):
        (run / 'packet').mkdir(parents=True, exist_ok=True)
        (run / 'packet' / (state + '.png')).write_bytes(b'png')
    write(run / 'packet/capture.json', {'app': 'designer', 'profile': 'chromium', 'states': []})
    write(run / 'runner.json', {'runner': 'codex-cli 1.0', 'requestedModel': None})
    write(run / 'report.json', {'app': 'designer', 'verdict': 'FINDINGS', 'coverage': {'captured': 2, 'assessed': 2},
                                'findings': [finding('A-F1', 'type-text', 'confirmed')],
                                'designDecisions': [finding('A-F2', 'term-picker-open-1440', 'design-decision', kind='design-proposal'),
                                                    finding('A-F3', 'type-text', 'uncertain', calibrated=False, kind='uncertain'),
                                                    finding('A-F4', 'type-text', kind='uncertain')],
                                'dismissed': [finding('A-F5', 'type-text', 'dismissed', calibrated=False)],
                                'blockers': []})
    aspect = lambda status: {'status': status, 'observation': 'Checked.'}
    write(run / 'coverage.json', {'states': [
        {'id': 'type-text', 'images': {'type-text.png': 'x'}, 'calibrated': True,
         'assessment': {'visualObservation': 'A card.', 'aspects': {'alignment': aspect('acceptable')}}},
        {'id': 'term-picker-open-1440', 'images': {'term-picker-open-1440.png': 'x'}, 'calibrated': True,
         'assessment': {'visualObservation': 'A dialog.', 'aspects': {'alignment': aspect('concern')}}}]})
    single = reports / 'ui-review/20261115-designer-pilot'
    write(single / 'capture.json', {'states': [{'id': 'empty'}]})
    write(single / 'review.json', {'inspectedStates': ['empty'], 'findings': [
        {'id': 'D-01', 'title': 'Toolbar crowds the title', 'kind': 'defect', 'severity': 'P2', 'observed': 'Crowded.',
         'evidence': ['empty.png', {'path': 'empty.png', 'sha256': 'x'}, {'sha256': 'no path'}]}]})
    (single / 'empty.png').write_bytes(b'png')
    write(reports / 'ui-review-board/decisions.json', {'decisions': [
        {'slug': 'icons', 'title': 'Preview Icons', 'summary': 'Should Preview keep its icons?',
         'background': 'Preview shows an icon beside each field name.',
         'options': [{'key': '1', 'label': 'Keep', 'recommended': True}, {'key': '2', 'label': 'Remove'}],
         'steps': [{'path': 'ui-review/20261115-designer-pilot/empty.png', 'title': 'empty', 'caption': 'The icons beside the names.'}]}]})
    os.utime(single / 'review.json', (time.time() - 100, time.time() - 100))
    return reports


def later_run(reports):
    """A second review that reports the first one's Save problem in other words, and a new one."""
    run = reports / 'ui-review-coordinator/designer-later'
    (run / 'packet').mkdir(parents=True)
    (run / 'packet/type-text.png').write_bytes(b'png')
    write(run / 'packet/capture.json', {'app': 'designer', 'profile': 'webkit-touch', 'states': []})
    write(run / 'report.json', {'app': 'designer', 'verdict': 'FINDINGS', 'coverage': {}, 'blockers': [],
        'findings': [finding('B-F1', 'type-text', 'confirmed', title='The Save action covers the field name',
                             observed='Save covers the end of the name.'),
                     finding('B-F2', 'term-picker-open-1440', 'confirmed', title='Release counts read of 2',
                             target='Release cell', observed='The release cell is empty.')],
        'designDecisions': [], 'dismissed': []})
    return 'coordinated/designer-later'


def tree_digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and 'ui-review-board' not in p.parts}


class BoardContentTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Client checks need Node')
    def test_client_note_ordering(self):
        result = subprocess.run(['node', '--test', str(Path(__file__).with_name('ui-review-board-client.test.mjs'))],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.reports = fixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_discovers_coordinated_and_single_reviews_newest_first(self):
        runs = board.discover_runs(self.reports)
        self.assertEqual(['coordinated/designer-run', 'single/20261115-designer-pilot'], [r['id'] for r in runs])
        self.assertEqual(('chromium', 'codex-cli 1.0', 5, 1),
                         (runs[0]['profile'], runs[0]['runner'], runs[0]['findings'], runs[0]['confirmed']))
        self.assertEqual('designer', runs[1]['app'], 'a capture that names no application predates the other collectors')

    def test_statuses_follow_the_report_section_and_verifier(self):
        contents = board.run_contents(self.reports, 'coordinated/designer-run')
        by_id = {i['id'].rsplit('/', 1)[1]: i for i in contents['items']}
        self.assertEqual({'A-F1': 'confirmed', 'A-F2': 'design', 'A-F3': 'uncertain', 'A-F4': 'not-verified', 'A-F5': 'dismissed'},
                         {k: v['status'] for k, v in by_id.items()})
        self.assertTrue(by_id['A-F3']['provisional'])
        self.assertFalse(by_id['A-F5']['provisional'], 'a dismissed finding is not shown as provisional')
        self.assertEqual('Save overlaps the name', by_id['A-F1']['title'])
        self.assertEqual(('Designer', 'Term picker'), (by_id['A-F1']['area'], by_id['A-F2']['area']))
        self.assertEqual(['type-text.png'], [i['label'] for i in by_id['A-F1']['images']],
                         'only image files that exist are offered')
        self.assertEqual(['The card, where Save covers the name.'], [i['caption'] for i in by_id['A-F1']['images']])
        self.assertEqual([''], [i['caption'] for i in by_id['A-F3']['images']], 'a review from before captions shows none')
        self.assertEqual({'type-text': 'passed', 'term-picker-open-1440': 'raised'},
                         {s['title']: s['status'] for s in contents['states']})

    def test_an_uncaptioned_screen_is_titled_by_state_and_described_by_the_reviewers_note(self):
        run = self.reports / 'ui-review-coordinator/designer-run'
        (run / 'packet/type-text-card-1.png').write_bytes(b'png')
        write(run / 'packet/capture.json', {'app': 'designer', 'profile': 'chromium', 'states': [
            {'id': 'type-text', 'screenshot': 'type-text.png', 'crops': ['type-text-card-1.png']},
            {'id': 'term-picker-open-1440', 'screenshot': 'term-picker-open-1440.png', 'crops': []}]})
        report = read_json(run / 'report.json')
        report['designDecisions'][1]['evidence'] = ['type-text.png', 'type-text-card-1.png']
        write(run / 'report.json', report)
        by_id = {i['id'].rsplit('/', 1)[1]: i for i in board.run_contents(self.reports, 'coordinated/designer-run')['items']}
        self.assertEqual([('type-text', 'A card.', True), ('type-text, card 1', 'A card.', True)],
                         [(i['label'], i['caption'], i['screenNote']) for i in by_id['A-F3']['images']])
        self.assertEqual([('type-text', 'The card, where Save covers the name.', False)],
                         [(i['label'], i['caption'], i['screenNote']) for i in by_id['A-F1']['images']],
                         "a reviewer's caption for the finding wins over the note on the screen")

    def test_all_screens_come_from_the_newest_capture_of_each_application_and_profile(self):
        older = self.reports / 'ui-review/automatic/20261120/workspace'
        newer = self.reports / 'ui-review/automatic/20261125/workspace'
        touch = self.reports / 'ui-review/workspace-touch'
        for directory, profile, states in ((older, 'chromium', ['old']), (newer, 'chromium', ['listing', 'missing']),
                                           (touch, 'webkit-touch', ['listing'])):
            write(directory / 'capture.json', {'app': 'workspace', 'profile': profile,
                                               'states': [{'id': s, 'screenshot': s + '.png', 'note': 'Seen.'} for s in states]})
            for s in states:
                if s != 'missing':
                    (directory / (s + '.png')).write_bytes(b'png')
        os.utime(older / 'capture.json', (time.time() - 1000, time.time() - 1000))
        mouse = {c['app']: c for c in board.screens(self.reports, 'chromium')}
        self.assertEqual('automatic/20261125/workspace', mouse['workspace']['id'])
        self.assertEqual([('listing', 'Seen.')], [(s['id'], s['note']) for s in mouse['workspace']['screens'] if s['src']],
                         'a state whose screenshot is missing is left out')
        self.assertEqual(['workspace'], [c['app'] for c in board.screens(self.reports, 'webkit-touch')])
        self.assertTrue(mouse['workspace']['screens'][0]['src'].startswith('/image?path=ui-review/automatic/20261125/workspace/'))

    def test_area_names_cee_only_for_findings_on_preview_alone(self):
        self.assertEqual('CEE Preview', board.designer_area(['type-image-preview', 'phone-preview-375']))
        self.assertEqual('Designer', board.designer_area(['empty', 'single-preview-a']))
        self.assertEqual('Term picker', board.designer_area(['type-text-preview', 'term-picker-open-1440']))

    def test_partial_capture_does_not_replace_complete_evidence_and_can_be_selected(self):
        root = self.reports / 'ui-review'
        for name, complete in [('full', True), ('partial', False)]:
            directory = root / name
            write(directory / 'capture.json', {'app': 'workspace', 'profile': 'chromium',
                'provenance': {'current': True, 'build': 'build-a'}, 'servedVerified': True,
                'states': [{'id': 'listing', 'screenshot': 'listing.png'}],
                'incomplete': [] if complete else [{'state': 'dialog', 'error': 'Timeout'}]})
            write(directory / 'coverage.json', {'scenarios': [{'id': 'listing', 'capture': 'captured'},
                *([] if complete else [{'id': 'dialog', 'capture': 'blocked'}])]})
            (directory / 'listing.png').write_bytes(b'png')
        os.utime(root / 'full/capture.json', (time.time() - 100, time.time() - 100))
        chosen = next(c for c in board.screens(self.reports, 'chromium') if c['app'] == 'workspace')
        self.assertEqual('full', chosen['id'])
        self.assertTrue(chosen['newerAvailable'])
        partial = next(c for c in board.screens(self.reports, 'chromium', ['partial']) if c['app'] == 'workspace')
        self.assertEqual(['dialog'], partial['missing'])
        self.assertEqual('partial', partial['status'])
        self.assertEqual(2, sum(row['required'] for row in partial['coverageRows']))
        self.assertEqual(1, sum(row['captured'] for row in partial['coverageRows']))
        with self.assertRaises(ValueError):
            board.screens(self.reports, 'webkit-touch', ['partial'])
        (root / 'full/listing.png').unlink()
        self.assertNotEqual('complete', board.latest_captures(self.reports)[('chromium', 'workspace')]['status'])

    def test_comparison_and_human_inspection_follow_evidence_without_editing_packets(self):
        root = self.reports / 'ui-review'
        for name, states in [('before', ['same', 'changed', 'gone']), ('after', ['same', 'changed', 'new'])]:
            directory = root / name
            write(directory / 'capture.json', {'app': 'workspace', 'profile': 'chromium',
                'provenance': {'current': True}, 'servedVerified': True,
                'states': [{'id': s, 'screenshot': s+'.png'} for s in states]})
            write(directory / 'coverage.json', {'scenarios': [{'id': s, 'capture': 'captured'} for s in states]})
            for state in states:
                (directory / (state+'.png')).write_bytes((name if state == 'changed' else state).encode())
        os.utime(root / 'before/capture.json', (time.time()-100, time.time()-100))
        def rows():
            return {s['id']: s for c in board.screens(self.reports, 'chromium') if c['app']=='workspace' for s in c['screens']}
        current = rows()
        self.assertEqual({'same':'unchanged','changed':'changed','new':'new','gone':'removed'},
                         {k:v['change'] for k,v in current.items()})
        path = root / 'after/capture.json'
        capture = read_json(path)
        capture['states'][1].update(title='Changed sample', description='The intended scenario setup.')
        write(path, capture)
        current = rows()
        self.assertEqual('Changed sample', current['changed']['title'])
        self.assertEqual('The intended scenario setup.', current['changed']['description'])
        before = tree_digest(self.reports)
        body = {'capture':'after','state':'changed','evidenceId':current['changed']['evidenceId'],
                'status':'attention','note':'The name is clipped.'}
        board.record_screen_note(self.reports, body)
        self.assertEqual('attention', rows()['changed']['human']['status'])
        self.assertEqual(before, tree_digest(self.reports))
        (root/'after/changed.png').write_bytes(b'revised')
        self.assertIsNone(rows()['changed']['human'])
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            board.record_screen_note(self.reports, body)
        self.assertEqual('Designer', board.designer_area([]))

    def test_single_reviews_are_shown_unverified(self):
        items = board.run_contents(self.reports, 'single/20261115-designer-pilot')['items']
        self.assertEqual([('Toolbar crowds the title', 'not-verified', 2)],
                         [(i['title'], i['status'], len(i['images'])) for i in items])

    def test_unknown_or_escaping_reviews_are_refused(self):
        for run_id in ('coordinated/../../outside', 'other/designer-run', 'coordinated/', 'single/../ui-review-board'):
            with self.subTest(run_id=run_id), self.assertRaises((ValueError, OSError)):
                board.run_contents(self.reports, run_id)

    def test_decisions_offer_only_existing_evidence(self):
        decision, = board.decisions(self.reports)
        self.assertEqual(('decision/icons', ['empty']), (decision['id'], [i['label'] for i in decision['images']]))

    def test_a_decision_out_of_the_agreed_form_says_what_it_lacks(self):
        self.assertEqual([], board.decisions(self.reports)[0]['problems'])
        write(self.reports / 'ui-review-board/decisions.json', {'decisions': [
            {'slug': 'bare', 'title': 'Bare', 'summary': 'Choose.', 'options': [{'key': '1', 'label': 'Keep', 'recommended': True},
                                                                              {'key': '2', 'label': 'Change', 'recommended': True}],
             'evidence': ['ui-review/20261115-designer-pilot/empty.png']},
            {'slug': 'captionless', 'title': 'Captionless', 'summary': 'Keep it?', 'background': 'A user gets here by saving.',
             'options': [{'key': '1', 'label': 'Keep'}, {'key': '2', 'label': 'Change'}],
             'steps': [{'path': 'ui-review/20261115-designer-pilot/empty.png', 'title': 'Saved'},
                       {'path': 'ui-review/absent.png', 'title': 'Gone', 'caption': 'Not captured.'}]}]})
        bare, captionless = board.decisions(self.reports)
        self.assertEqual(['no question', 'no background', 'no screens', 'more than one recommended'],
                         [problem.split(':')[0].split(' saying')[0].split(' option')[0] for problem in bare['problems']])
        self.assertEqual(['screen 1 has no caption saying what it shows', "screen 2 has no screenshot at 'ui-review/absent.png'"],
                         captionless['problems'])
        lines = []
        self.assertEqual(1, board.plan(self.reports, printer=lines.append), 'the plan fails while a decision is not ready')
        self.assertTrue(any(line.startswith('  bare is not ready to decide: no question') for line in lines), lines)

    def test_decision_steps_keep_their_titles_and_captions_in_order(self):
        write(self.reports / 'ui-review-board/decisions.json', {'decisions': [
            {'slug': 'cues', 'title': 'Cues', 'summary': 'Should rows say they open?', 'options': [], 'steps': [
                {'path': 'ui-review/20261115-designer-pilot/empty.png', 'title': 'Before', 'caption': 'Nothing opened.'},
                {'path': 'ui-review/absent.png', 'title': 'Missing', 'caption': 'Not captured.'},
                {'path': 'ui-review/20261115-designer-pilot/empty.png', 'title': 'After', 'caption': 'Opened.'}]}]})
        decision, = board.decisions(self.reports)
        self.assertEqual([('Before', 'Nothing opened.'), ('After', 'Opened.')],
                         [(i['label'], i['caption']) for i in decision['images']], 'a step whose screen is missing is left out')

    def test_answers_are_validated_and_kept_beside_the_reports(self):
        before = tree_digest(self.reports)
        first = 'coordinated/designer-run/A-F1'
        stored = board.record_answer(self.reports, {'id': first, 'status': 'needs-fix', 'note': 'Move Save.'})
        self.assertEqual({first: 'designer-0001'}, {k: v['issue'] for k, v in stored['links'].items()})
        self.assertEqual(('needs-fix', None, 'Move Save.', 'Save overlaps the name'),
                         tuple(stored['issues']['designer-0001'][k] for k in ('status', 'option', 'note', 'title')))
        board.record_answer(self.reports, {'id': 'decision/icons', 'status': 'option', 'option': '1'})
        board.record_answer(self.reports, {'id': 'coordinated/designer-run/state/type-text', 'status': 'approved'})
        self.assertEqual({'designer-0001', 'decision/icons', 'coordinated/designer-run/state/type-text'},
                         set(board.answers(self.reports)['issues']))
        self.assertEqual(before, tree_digest(self.reports), 'retained reviews are never written')
        for bad in ({'id': first, 'status': 'maybe'}, {'id': 'bad id with spaces', 'status': 'approved'},
                    {'id': first, 'status': 'option'}, {'id': first, 'status': 'approved', 'note': 'n' * 4001},
                    {'id': 'coordinated/designer-run/A-F9', 'status': 'approved'},
                    {'id': 'coordinated/../../x/A-F1', 'status': 'approved'}, 'not an object'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                board.record_answer(self.reports, bad)
        cleared = board.record_answer(self.reports, {'id': first, 'clear': True})
        self.assertNotIn(first, cleared['links'])
        self.assertNotIn('designer-0001', cleared['issues'], 'an issue no finding is linked to is dropped')

    def test_a_later_review_suggests_the_issue_and_a_link_shares_its_answer(self):
        later = later_run(self.reports)
        self.assertEqual([[], []], [i['matches'] for i in board.run_contents(self.reports, later)['items']],
                         'nothing answered, nothing suggested')
        first = 'coordinated/designer-run/A-F1'
        board.record_answer(self.reports, {'id': first, 'status': 'needs-fix', 'note': 'Move Save.'})
        repeat, unrelated = board.run_contents(self.reports, later)['items']
        self.assertEqual(['designer-0001'], [m['issue'] for m in repeat['matches']])
        self.assertTrue(repeat['matches'][0]['likely'], repeat['matches'][0])
        self.assertEqual([], unrelated['matches'])
        same = board.run_contents(self.reports, 'coordinated/designer-run')['items']
        self.assertEqual('designer-0001', same[0]['issue'])
        self.assertNotIn('designer-0001', [m['issue'] for m in same[0]['matches']], 'a linked finding is not offered its own issue')

        stored = board.record_answer(self.reports, {'id': repeat['id'], 'link': 'designer-0001'})
        self.assertEqual('designer-0001', stored['links'][repeat['id']]['issue'])
        stored = board.record_answer(self.reports, {'id': repeat['id'], 'status': 'deferred'})
        self.assertEqual((1, 'deferred'), (len(stored['issues']), stored['issues']['designer-0001']['status']),
                         'answering either finding answers the shared issue')
        stored = board.record_answer(self.reports, {'id': repeat['id'], 'clear': True})
        self.assertEqual(('deferred', [first]), (stored['issues']['designer-0001']['status'], list(stored['links'])),
                         'unlinking one finding keeps the answer for the other')
        board.record_answer(self.reports, {'id': unrelated['id'], 'status': 'approved'})
        self.assertIn('designer-0002', board.answers(self.reports)['issues'])
        for bad in ({'id': repeat['id'], 'link': 'designer-0099'}, {'id': repeat['id'], 'link': 'decision/icons'},
                    {'id': 'decision/icons', 'link': 'designer-0001'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                board.record_answer(self.reports, bad)


class BoardServerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.reports = fixture(Path(self.temporary.name))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), board.make_handler(self.reports))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temporary.cleanup()

    def get(self, path):
        with urlopen(self.base + path) as response:
            return response.status, response.headers.get('Content-Type'), response.read()

    def post(self, body, origin=None):
        request = Request(self.base + '/api/answer', data=json.dumps(body).encode(), method='POST',
                          headers={'Content-Type': 'application/json', **({'Origin': origin} if origin else {})})
        with urlopen(request) as response:
            return response.status, json.loads(response.read())

    def status(self, call):
        with self.assertRaises(HTTPError) as caught:
            call()
        return caught.exception.code

    def test_serves_the_page_index_review_and_images(self):
        self.assertIn(b'UI Review Board', self.get('/')[2])
        index = json.loads(self.get('/api/index')[2])
        self.assertEqual((2, 1, {'issues': {}, 'links': {}}), (len(index['runs']), len(index['decisions']), index['answers']))
        contents = json.loads(self.get('/api/run?id=coordinated/designer-run')[2])
        code, kind, body = self.get(contents['items'][0]['images'][0]['src'])
        self.assertEqual((200, 'image/png', b'png'), (code, kind, body))

    def test_refuses_paths_outside_the_reports_and_non_images(self):
        outside = Path(self.temporary.name) / 'secret.png'
        outside.write_bytes(b'secret')
        for path in ('/image?path=../secret.png', '/image?path=ui-review-coordinator/designer-run/report.json',
                     '/image?path=ui-review/absent.png'):
            with self.subTest(path=path):
                self.assertEqual(404, self.status(lambda: self.get(path)))
        self.assertEqual(400, self.status(lambda: self.get('/api/run?id=coordinated/../..')))

    def test_records_answers_only_from_the_board(self):
        self.assertEqual(403, self.status(lambda: self.post({'id': 'decision/icons', 'status': 'deferred'}, 'https://example.org')))
        self.assertEqual(400, self.status(lambda: self.post({'id': 'decision/icons', 'status': 'maybe'})))
        code, stored = self.post({'id': 'decision/icons', 'status': 'deferred'}, self.base)
        self.assertEqual((200, 'deferred'), (code, stored['issues']['decision/icons']['status']))
        self.assertEqual(stored, board.answers(self.reports))


class BoardCommandTest(unittest.TestCase):
    def test_plan_lists_reviews_without_serving(self):
        with tempfile.TemporaryDirectory() as temporary:
            reports = fixture(Path(temporary))
            result = CliRunner().invoke(test_processes.app, ['ui-review-board', '--plan', '--reports', str(reports)])
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn('2 retained reviews', result.output)
        self.assertIn('coordinated/designer-run', result.output)
        self.assertIn('1 design decisions, 0 answered issues covering 0 linked findings', result.output)


if __name__ == '__main__':
    unittest.main()

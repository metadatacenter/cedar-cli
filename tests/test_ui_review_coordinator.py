import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from typer.testing import CliRunner

from org.metadatacenter import ui_review_coordinator as c, test_processes


def fixture(root, count=3, app='designer'):
    home = root / 'home'
    guide = home / 'cedar-development/ops/FRONTEND-RUNBOOK.md'
    guide.parent.mkdir(parents=True)
    guide.write_text('###### Agreed Layout Behavior\nKeep full field names.\n###### Retained Evidence\n')
    packet = root / 'original'
    packet.mkdir()
    states, files = [], {}
    for i in range(count):
        id_ = 'supplemental' if i == count - 1 else f'state-{i}'
        state = {'id': id_, 'screenshot': id_ + '.png', 'crops': [id_ + '-crop.png'],
                 'aria': id_ + '.yml', 'metrics': id_ + '.json'}
        for name in c.state_images(state):
            (packet / name).write_bytes(b'fake PNG for offline tests only: ' + id_.encode())
        (packet / state['aria']).write_text('button: Save')
        (packet / state['metrics']).write_text(json.dumps({'viewport': {'width': 800, 'height': 600},
                                                          'controls': [], 'regions': []}))
        for name in [*c.state_images(state), state['aria'], state['metrics']]:
            files[name] = c.digest((packet / name).read_bytes())
        states.append(state)
    c.write_json(packet / 'capture.json', {'app': app, 'files': files, 'states': states,
        'provenance': {'current': True}, 'automaticChecks': {'groups': []}})
    cal = packet / 'calibration'
    cal.mkdir()
    (cal / 'case.png').write_bytes(b'calibration fixture')
    c.write_json(cal / 'packet.json', [{'id': 'case', 'images': ['case.png'], 'actions': [],
        'imageSha256': {'case.png': c.digest((cal / 'case.png').read_bytes())}}])
    return home, packet


def assessment(state):
    return {'id': state['id'], 'images': c.state_images(state),
            'visualObservation': 'Save is visible above the empty field area.',
            'aspects': {a: {'status': 'acceptable', 'observation': 'Recorded evidence is coherent.'}
                        for a in c.ASPECTS}}


def finding(id_, state):
    return {'id': id_, 'title': 'Save overlaps field name', 'target': 'Save', 'category': 'alignment',
            'observed': 'Save overlaps the name.', 'expected': 'Keep the name and action separate.',
            'impact': 'The name cannot be read.', 'kind': 'defect', 'severity': 'medium',
            'stateIds': [state['id']], 'steps': ['Open the recorded state.'],
            'evidence': [state['screenshot']],
            'evidenceCaptions': [{'file': state['screenshot'], 'caption': 'The card header, where Save covers the end of the name.'}]}


class OfflineRunner:
    """A protocol stand-in, never evidence that a real model inspected an image."""
    def __init__(self, packet, *, omit=None, candidates=False, fail=None):
        self.states = c.read_json(packet / 'capture.json')['states']
        self.calibration = c.read_json(packet / 'calibration/packet.json')
        self.omit, self.candidates, self.fail = omit, candidates, fail
        self.calls = []

    def run(self, directory, prompt, images, schema, *, resume=None):
        self.calls.append((directory, resume, images))
        if self.fail and self.fail(directory):
            raise ValueError('Injected runner interruption')
        supplied = {p.name for p in images}
        selected = [s for s in self.states if s['screenshot'] in supplied]
        if 'assessments' in schema['properties']:
            result = {'assessments': [assessment(s) for s in selected if s['id'] != self.omit], 'findings': []}
            if self.candidates and selected:
                result['findings'] = [finding('candidate', selected[0])]
        elif 'decisions' in schema['properties']:
            candidates = json.loads(prompt.split('\nCandidates:\n')[1])
            result = {'decisions': [{'id': f['id'], 'decision': 'confirmed',
                                     'reason': 'Overlap is visible in the supplied screenshot.',
                                     'evidence': f['evidence']} for f in candidates]}
        elif 'groups' in schema['properties']:
            findings = json.loads(prompt.split('\nFindings:\n')[1])
            grouped = {}
            for f in findings:
                grouped.setdefault((f['kind'], f['category'], f['target']), []).append(f['id'])
            result = {'groups': [{'representativeId': ids[0], 'memberIds': ids} for ids in grouped.values()]}
        else:
            result = {'cases': [{'id': case['id'], 'issue': False, 'reason': 'Offline stand-in; no visual judgment.'}
                                for case in self.calibration]}
        directory.mkdir(parents=True)
        c.write_json(directory / 'schema.json', schema)
        c.write_json(directory / 'response.json', result)
        (directory / 'prompt.txt').write_text(prompt)
        (directory / 'events.jsonl').write_text('offline protocol stand-in\n')
        receipt = {'thread': resume or 'offline-' + directory.parent.name, 'resumeThread': resume,
            'promptSha256': c.digest(prompt.encode()), 'schemaSha256': c.digest(c.encoded(schema)),
            'images': [{'file': str(p), 'sha256': c.digest(p.read_bytes())} for p in images],
            'responseSha256': c.digest(c.encoded(result)),
            'eventsSha256': c.digest((directory / 'events.jsonl').read_bytes())}
        c.write_json(directory / 'receipt.json', receipt)
        return result, receipt


class OfflineHarness:
    def __init__(self, failed_calibration=False, blockers=()):
        self.failed_calibration = failed_calibration
        self.capture_blockers = list(blockers)

    def blockers(self, packet):
        return self.capture_blockers

    def score(self, packet, job):
        return {'detected': 0, 'defects': int(self.failed_calibration), 'falseAlarms': 0}


class CoordinatorTest(unittest.TestCase):
    def test_oversized_verification_union_splits_without_losing_candidates_or_related_images(self):
        candidates = [{'id': 'a', 'job': 'batch-001'}, {'id': 'b', 'job': 'batch-001'}]
        def request(rows):
            return 'evidence' * 100 * len(rows), [name for row in rows
                                                for name in (row['id'] + '.png', row['id'] + '-follow-up.png')]
        jobs = c.bounded_verification_jobs([{'id': 'batch-001'}], candidates, request, limit=1000)
        self.assertEqual([j['id'] for j in jobs], ['batch-001-001', 'batch-001-002'])
        self.assertEqual([f for j in jobs for f in j['candidates']], candidates)
        self.assertEqual([p for j in jobs for p in j['images']],
                         ['a.png', 'a-follow-up.png', 'b.png', 'b-follow-up.png'])
        self.assertTrue(all(len(j['prompt']) <= 1000 and j['error'] is None for j in jobs))
        too_large = c.bounded_verification_jobs([{'id': 'batch-001'}], candidates[:1], request, limit=500)
        self.assertIn('no evidence was omitted', too_large[0]['error'])
        self.assertEqual(too_large[0]['prompt'], request(candidates[:1])[0])

    def test_evidence_tables_preserve_values_missing_keys_and_control_order(self):
        controls = []
        for index in range(1000):
            row = {'tag': 'button', 'id': str(index), 'label': 'Move field down',
                   'x': 10.125, 'y': index * 24, 'width': 24, 'height': 24,
                   'intrinsicTextWidth': 110, 'paddingInline': ['0px', '0px'],
                   'gap': 'normal', 'disabled': False, 'expanded': None,
                   'pointerTarget': {'x': 22.125, 'y': index * 24 + 12, 'belongsToControl': True}}
            if index % 2:
                del row['expanded']
            controls.append(row)
        states = [{'id': 'state', 'images': ['state.png'], 'accessibility': 'button: Move field down',
                   'geometry': {'viewport': {'width': 800, 'height': 600},
                                'controls': controls, 'regions': [], 'controlsOmitted': 3}}]
        original = copy.deepcopy(states)
        text = c.evidence_json(states)
        restored = json.loads(text)['states']
        for state in restored:
            for group in ('controls', 'regions'):
                indexed = []
                for table in state['geometry'][group]:
                    indexed.extend((index, dict(zip(table['columns'], values)))
                                   for index, values in zip(table['indices'], table['rows']))
                state['geometry'][group] = [row for _, row in sorted(indexed)]
        self.assertEqual(restored, original)
        self.assertEqual(states, original)
        self.assertLess(len(text), len(json.dumps(original)) * 0.7)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home, self.packet = fixture(self.root)
        self.output = self.root / 'review'

    def run_coordinator(self, **kwargs):
        return c.coordinate(self.packet, self.output, self.home, batch_size=2,
                            runner=kwargs.pop('runner', OfflineRunner(self.packet)),
                            harness=kwargs.pop('harness', OfflineHarness()), printer=lambda *_: None, **kwargs)

    def test_any_catalogued_application_is_reviewed_under_its_own_name(self):
        home, packet = fixture(self.root / 'openview', app='openview')
        output = self.root / 'openview-review'
        runner = OfflineRunner(packet)
        self.assertEqual(0, c.coordinate(packet, output, home, batch_size=2, runner=runner, harness=OfflineHarness(),
                                         printer=lambda *_: None))
        report = c.read_json(output / 'report.json')
        self.assertEqual('openview', report['app'])
        self.assertTrue(report['scope'].startswith('Review of recorded OpenView evidence'), report['scope'])
        self.assertIn('<h1>OpenView: REVIEWED</h1>', (output / 'report.html').read_text())
        self.assertTrue(all('assembled CEDAR OpenView' in (d / 'prompt.txt').read_text()
                            for d, _, _ in runner.calls if (d / 'prompt.txt').exists() and d.name == 'assessment'))
        self.assertEqual('designer', c.make_plan(self.packet, self.home, 2)['app'])
        _, unknown = fixture(self.root / 'unknown', app='monitoring')
        with self.assertRaisesRegex(ValueError, 'no application the review catalog covers'):
            c.make_plan(unknown, home, 2)

    def test_plan_covers_all_captured_states_including_supplemental_without_calling_ai(self):
        plan = c.make_plan(self.packet, self.home, 2)
        self.assertEqual([id_ for job in plan['jobs'] for id_ in job['states']], plan['states'])
        self.assertIn('supplemental', plan['states'])
        with patch.object(c, 'CodexRunner', side_effect=AssertionError('must not launch')):
            self.assertEqual(self.run_coordinator(plan_only=True), 0)
        self.assertFalse(self.output.exists())

    def test_complete_run_accounts_for_every_state_and_image_and_preserves_original(self):
        before = {str(p): p.read_bytes() for p in self.packet.rglob('*') if p.is_file()}
        runner = OfflineRunner(self.packet)
        self.assertEqual(self.run_coordinator(runner=runner), 0)
        ledger = c.read_json(self.output / 'coverage.json')['states']
        self.assertEqual({r['id'] for r in ledger}, {'state-0', 'state-1', 'supplemental'})
        self.assertTrue(all(r['status'] == 'assessed' and r['calibrated'] and len(r['images']) == 2 for r in ledger))
        for row in ledger:
            receipt = c.read_json(self.output / row['receipt'])
            supplied = {Path(i['file']).name for i in receipt['images']}
            self.assertLessEqual(set(row['images']), supplied)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.packet.rglob('*') if p.is_file()})
        self.assertTrue((self.output / 'report.html').exists())
        self.assertTrue(all(resume for directory, resume, _ in runner.calls if directory.name == 'calibration'))

    def test_omitted_state_never_passes_even_if_audit_reviewer_assesses_it(self):
        self.assertEqual(self.run_coordinator(runner=OfflineRunner(self.packet, omit='supplemental')), 2)
        report = c.read_json(self.output / 'report.json')
        self.assertIn('supplemental', report['coverage']['missing'])

    def test_changed_or_unhashed_image_is_rejected_before_runner(self):
        (self.packet / 'state-0.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed since capture'):
            self.run_coordinator()
        self.assertFalse(self.output.exists())

    def test_related_evidence_must_name_a_captured_state(self):
        capture = c.read_json(self.packet / 'capture.json')
        capture['states'][0]['relatedStates'] = ['invented-follow-up']
        c.write_json(self.packet / 'capture.json', capture)
        with self.assertRaisesRegex(ValueError, 'uncaptured state'):
            self.run_coordinator()
        self.assertFalse(self.output.exists())

    def test_verifier_receives_related_follow_up_from_another_batch(self):
        capture = c.read_json(self.packet / 'capture.json')
        capture['states'][0]['relatedStates'] = ['supplemental']
        c.write_json(self.packet / 'capture.json', capture)
        runner = OfflineRunner(self.packet, candidates=True)
        self.run_coordinator(runner=runner)
        supplied = next(images for directory, _, images in runner.calls
                        if directory.parent.name == 'verify-batch-001' and directory.name == 'assessment')
        self.assertIn((self.output / 'packet/supplemental.png').resolve(), supplied)
        self.assertIn((self.output / 'packet/supplemental-crop.png').resolve(), supplied)

    def test_symlink_image_and_path_escape_are_rejected(self):
        path = self.packet / 'state-0.png'
        path.unlink()
        path.symlink_to(self.packet / 'state-1.png')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            c.inspect_packet(self.packet)
        for name in ['../secret', '/secret', 'sub/file', '..', 'a\\b']:
            with self.assertRaises(ValueError):
                c.safe_file(self.packet, name)

    def test_legacy_unhashed_calibration_is_refused(self):
        cal = c.read_json(self.packet / 'calibration/packet.json')
        del cal[0]['imageSha256']
        c.write_json(self.packet / 'calibration/packet.json', cal)
        with self.assertRaisesRegex(ValueError, 'unhashed'):
            self.run_coordinator()

    def test_failed_calibration_is_incomplete_and_not_rerolled_on_resume(self):
        runner = OfflineRunner(self.packet)
        harness = OfflineHarness(failed_calibration=True)
        self.assertEqual(self.run_coordinator(runner=runner, harness=harness), 2)
        calls = len(runner.calls)
        self.assertEqual(self.run_coordinator(runner=runner, harness=harness, resume=True), 2)
        self.assertEqual(len(runner.calls), calls)

    def test_calibration_failure_does_not_hide_complete_coverage_or_imply_trusted_findings(self):
        self.assertEqual(self.run_coordinator(runner=OfflineRunner(self.packet, candidates=True),
                                             harness=OfflineHarness(failed_calibration=True)), 2)
        report = c.read_json(self.output / 'report.json')
        self.assertEqual(report['coverage'], {'captured': 3, 'assessed': 3, 'missing': []})
        self.assertTrue(report['findings'])
        self.assertTrue(all(not f['calibrated'] for f in report['findings']))
        self.assertIn('Provisional:', (self.output / 'report.html').read_text())

    def test_resume_reuses_success_and_retries_only_interrupted_jobs(self):
        runner = OfflineRunner(self.packet, fail=lambda directory: directory.parent.name == 'batch-002')
        self.assertEqual(self.run_coordinator(runner=runner), 2)
        resumed = OfflineRunner(self.packet)
        self.assertEqual(self.run_coordinator(runner=resumed, resume=True), 0)
        self.assertEqual({d.parent.name for d, _, _ in resumed.calls}, {'batch-002'})

    def test_resume_refuses_changed_response_or_configuration(self):
        self.run_coordinator()
        response = self.output / 'jobs/batch-001/assessment/response.json'
        changed = c.read_json(response)
        changed['assessments'][0]['visualObservation'] = 'Modified'
        c.write_json(response, changed)
        self.assertEqual(self.run_coordinator(resume=True), 2)
        self.assertTrue(any('changed' in e for e in c.read_json(self.output / 'report.json')['blockers']))
        with self.assertRaisesRegex(ValueError, 'partition changed'):
            self.run_coordinator(resume=True, model='different')

    def test_candidate_verification_deduplicates_without_dropping_occurrences(self):
        runner = OfflineRunner(self.packet, candidates=True)
        self.assertEqual(self.run_coordinator(runner=runner), 1)
        report = c.read_json(self.output / 'report.json')
        self.assertEqual(len(report['findings']), 1)
        self.assertGreater(len(report['findings'][0]['occurrences']), 1)
        self.assertEqual(report['coverage']['assessed'], 3)
        calls = len(runner.calls)
        self.assertEqual(self.run_coordinator(runner=runner, resume=True), 1)
        self.assertEqual(len(runner.calls), calls)

    def test_consolidation_cannot_drop_duplicate_or_mix_verdicts(self):
        state = c.read_json(self.packet / 'capture.json')['states'][0]
        findings = [finding('one', state), {**finding('two', state), 'kind': 'uncertain'}]
        bad_groups = [
            [{'representativeId': 'one', 'memberIds': ['one']}],
            [{'representativeId': 'one', 'memberIds': ['one', 'one', 'two']}],
            [{'representativeId': 'one', 'memberIds': ['one', 'two']}],
            [{'representativeId': 'two', 'memberIds': ['one']},
             {'representativeId': 'two', 'memberIds': ['two']}],
        ]
        for groups in bad_groups:
            runner = OfflineRunner(self.packet)
            with patch.object(runner, 'run', return_value=({'groups': groups}, {})):
                with self.assertRaises(ValueError):
                    c.consolidate_findings(self.output, findings, runner)

    def test_a_group_keeps_one_caption_for_each_image_its_occurrences_cite(self):
        one, two = c.read_json(self.packet / 'capture.json')['states'][:2]
        findings = [finding('one', one), {**finding('two', two), 'evidenceCaptions': [{'file': two['screenshot'], 'caption': 'Second.'}]},
                    {**finding('three', one), 'evidenceCaptions': [{'file': one['screenshot'], 'caption': 'A later wording.'}]}]
        group, = c.group_findings(findings, {'one': 'one', 'two': 'one', 'three': 'one'})
        self.assertEqual({one['screenshot']: 'The card header, where Save covers the end of the name.', two['screenshot']: 'Second.'},
                         {caption['file']: caption['caption'] for caption in group['evidenceCaptions']})

    def test_consolidation_retains_worst_severity_and_all_provenance(self):
        state = c.read_json(self.packet / 'capture.json')['states'][0]
        findings = [{**finding('one', state), 'calibrated': False, 'severity': 'high'},
                    {**finding('two', state), 'calibrated': True, 'severity': 'low'}]
        grouped = c.group_findings(findings, {'one': 'two', 'two': 'two'})
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]['id'], 'two')
        self.assertEqual(grouped[0]['severity'], 'high')
        self.assertFalse(grouped[0]['calibrated'])
        self.assertEqual({f['id'] for f in grouped[0]['occurrences']}, {'one', 'two'})

    def test_original_capture_blockers_and_automatic_findings_are_not_overridden(self):
        capture = c.read_json(self.packet / 'capture.json')
        violation = {'source': 'axe', 'rule': 'button-name', 'target': '.icon',
                     'detail': 'The icon button has no accessible name.', 'states': ['state-0'], 'exception': None}
        capture['automaticChecks']['groups'] = [violation, {**violation, 'exception': 'Accepted exception'}]
        c.write_json(self.packet / 'capture.json', capture)
        self.assertEqual(self.run_coordinator(harness=OfflineHarness(blockers=['Stale runtime'])), 2)
        report = c.read_json(self.output / 'report.json')
        self.assertIn('Stale runtime', report['blockers'])
        self.assertEqual(report['automaticFindings'], [violation])

    def test_output_inside_capture_and_existing_output_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            c.coordinate(self.packet, self.packet / 'review', self.home)
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_coordinator()

    def test_missing_crop_or_criterion_and_unsupported_finding_fail_validation(self):
        states = c.read_json(self.packet / 'capture.json')['states'][:1]
        correct = {'assessments': [assessment(states[0])], 'findings': []}
        missing_crop = copy.deepcopy(correct)
        missing_crop['assessments'][0]['images'].pop()
        missing_criterion = copy.deepcopy(correct)
        del missing_criterion['assessments'][0]['aspects']['alignment']
        unsupported = copy.deepcopy(correct)
        unsupported['findings'] = [finding('f', states[0])]
        unsupported['findings'][0]['evidence'] = ['not-supplied.png']
        concern_without_finding = copy.deepcopy(correct)
        concern_without_finding['assessments'][0]['aspects']['alignment']['status'] = 'concern'
        for bad in [missing_crop, missing_criterion, unsupported, concern_without_finding]:
            with self.assertRaises(ValueError):
                c.validate_assessments(bad, states)

    def test_every_cited_image_has_exactly_one_caption(self):
        states = c.read_json(self.packet / 'capture.json')['states'][:2]
        result = {'assessments': [assessment(s) for s in states], 'findings': [finding('f', states[0])]}
        c.validate_assessments(result, states)
        uncaptioned = copy.deepcopy(result)
        uncaptioned['findings'][0]['evidence'].append(states[0]['crops'][0])
        twice = copy.deepcopy(result)
        twice['findings'][0]['evidenceCaptions'].append(dict(twice['findings'][0]['evidenceCaptions'][0]))
        stray = copy.deepcopy(result)
        stray['findings'][0]['evidenceCaptions'].append({'file': states[0]['crops'][0], 'caption': 'A crop the finding does not cite.'})
        for bad in (uncaptioned, twice, stray):
            with self.subTest(bad=bad['findings'][0]['evidenceCaptions']), self.assertRaisesRegex(ValueError, 'caption each image'):
                c.validate_assessments(bad, states)
        schema = c.review_schema(states)
        self.assertEqual(sorted(c.state_images(states[0]) + c.state_images(states[1])),
                         schema['properties']['findings']['items']['properties']['evidenceCaptions']['items']['properties']['file']['enum'])

    def test_comparison_evidence_may_name_a_good_assigned_state(self):
        states = c.read_json(self.packet / 'capture.json')['states'][:2]
        result = {'assessments': [assessment(s) for s in states], 'findings': [finding('f', states[0])]}
        result['findings'][0]['evidence'].append(states[1]['screenshot'])
        result['findings'][0]['evidenceCaptions'].append({'file': states[1]['screenshot'], 'caption': 'The same header, unobstructed.'})
        c.validate_assessments(result, states)
        result['findings'][0]['evidence'] = [states[1]['screenshot']]
        with self.assertRaisesRegex(ValueError, 'affected state'):
            c.validate_assessments(result, states)

    def test_generation_schema_constrains_state_and_image_references(self):
        states = c.read_json(self.packet / 'capture.json')['states'][:2]
        result = {'assessments': [assessment(s) for s in states], 'findings': [finding('f', states[0])]}
        schema = c.review_schema(states)
        c.validate_shape(result, schema)
        result['findings'][0]['evidence'] = [states[0]['screenshot'] + ' shows an overlap']
        with self.assertRaisesRegex(ValueError, 'unsupported value'):
            c.validate_shape(result, schema)
        self.assertNotIn('enum', c.REVIEW_SCHEMA['properties']['assessments']['items']['properties']['id'])

    def test_unsealed_invalid_answer_gets_one_retained_retry(self):
        runner = OfflineRunner(self.packet, omit='supplemental')
        self.assertEqual(self.run_coordinator(runner=runner), 2)
        job = self.output / 'jobs/batch-002'
        self.assertTrue((job / 'invalid-assessment/receipt.json').exists())
        self.assertFalse((job / 'seal.json').exists())
        attempts = len(runner.calls)
        self.assertEqual(self.run_coordinator(runner=runner, resume=True), 2)
        self.assertEqual(len(runner.calls), attempts)
        self.assertEqual(sum(d.parent.name == 'batch-002' for d, _, _ in runner.calls), 2)

    def test_validator_revision_revalidates_saved_answers_without_rerolling(self):
        runner = OfflineRunner(self.packet)
        self.run_coordinator(runner=runner)
        calls = len(runner.calls)
        plan = c.read_json(self.output / 'plan.json')
        updated = {**plan, 'coordinatorSha256': 'new-validator-version'}
        with patch.object(c, 'make_plan', return_value=updated):
            self.assertEqual(self.run_coordinator(runner=runner, resume=True), 0)
        self.assertEqual(len(runner.calls), calls)
        self.assertEqual(c.read_json(self.output / 'coordinator-history.json'),
                         [plan['coordinatorSha256'], 'new-validator-version'])

    def test_cli_resolves_paths_from_the_callers_directory_and_preserves_exit_code(self):
        with patch.object(c, 'coordinate', return_value=2) as run, \
                patch('org.metadatacenter.util.InvocationContext.invocation_environment',
                      return_value={'CEDAR_CLI_CALLER_DIR': '/tmp/caller'}), \
                patch('org.metadatacenter.util.Util.Util.cedar_home', '/tmp/home'):
            result = CliRunner().invoke(test_processes.app, ['ui-review-coordinate', '--capture', 'packet',
                '--output', 'report', '--plan'])
            chosen = CliRunner().invoke(test_processes.app, ['ui-review-coordinate', '--capture', 'packet',
                '--output', 'report', '--reviewer', 'claude'])
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertEqual(run.call_args_list[0].args[:2], (Path('/tmp/caller/packet'), Path('/tmp/caller/report')))
        self.assertTrue(run.call_args_list[0].kwargs['plan_only'])
        self.assertEqual(('codex', 'claude'), tuple(call.kwargs['reviewer'] for call in run.call_args_list))
        self.assertEqual(chosen.exit_code, 2, chosen.output)

    def test_runner_delivers_images_on_stdin_protocol_and_records_completed_receipt(self):
        directory = self.root / 'runner'
        images = [self.packet / 'state-0.png']
        response = {'cases': [{'id': 'one', 'issue': False, 'reason': 'Visible control.'}]}
        launches = []

        class Process:
            returncode = 0

            def __init__(self, command, **kwargs):
                launches.append((command, kwargs))
                self.command, self.kwargs = command, kwargs

            def communicate(self, prompt, timeout):
                self.kwargs['stdout'].write(json.dumps({'type': 'thread.started', 'thread_id': 'fresh-reviewer'}) + '\n')
                self.kwargs['stdout'].write(json.dumps({'type': 'item.completed', 'item': {
                    'type': 'error', 'message': 'Code Mode is unavailable because code-mode host is disabled. '
                    'Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`.'}}) + '\n')
                self.kwargs['stdout'].write(json.dumps({'type': 'turn.completed', 'usage': {}}) + '\n')
                c.write_json(Path(self.command[self.command.index('-o') + 1]), response)

        with patch.object(c.shutil, 'which', return_value='/mock/codex'), \
                patch.object(c.subprocess, 'check_output', return_value='codex test'), \
                patch.object(c.subprocess, 'Popen', Process), \
                patch.dict(c.os.environ, {'CEDAR_PRIVATE_TOKEN': 'must-not-reach-reviewer'}):
            runner = c.CodexRunner()
            result, receipt = runner.run(directory, 'Review the image.', images, c.CALIBRATION_SCHEMA)
        self.assertEqual(result, response)
        self.assertEqual(receipt['thread'], 'fresh-reviewer')
        self.assertEqual(receipt['images'][0]['sha256'], c.digest(images[0].read_bytes()))
        command, options = launches[0]
        self.assertEqual(command[-2:], ['--', '-'])
        self.assertIn('--ignore-user-config', command)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('features.view_image=false', command)
        self.assertIn('project_doc_max_bytes=0', command)
        self.assertNotIn('CEDAR_PRIVATE_TOKEN', options['env'])
        self.assertNotEqual(Path(command[command.index('-C') + 1]), self.packet)

    def test_runner_rejects_tool_usage_even_with_valid_structured_output(self):
        class Process:
            returncode = 0

            def __init__(self, command, **kwargs):
                self.kwargs = kwargs

            def communicate(self, prompt, timeout):
                self.kwargs['stdout'].write(json.dumps({'type': 'item.completed',
                    'item': {'type': 'command_execution', 'command': 'cat source'}}) + '\n')

        with patch.object(c.shutil, 'which', return_value='/mock/codex'), \
                patch.object(c.subprocess, 'check_output', return_value='codex test'), \
                patch.object(c.subprocess, 'Popen', Process):
            runner = c.CodexRunner()
            with self.assertRaisesRegex(ValueError, 'unexpected tool'):
                runner.run(self.root / 'runner', 'Review.', [], c.CALIBRATION_SCHEMA)
        self.assertFalse((self.root / 'runner/receipt.json').exists())

    def test_runner_timeout_stops_process_before_a_receipt_can_exist(self):
        class Process:
            returncode = None

            def __init__(self, *args, **kwargs):
                pass

            def communicate(self, *args, **kwargs):
                raise subprocess.TimeoutExpired('codex', 1)

        with patch.object(c.shutil, 'which', return_value='/mock/codex'), \
                patch.object(c.subprocess, 'check_output', return_value='codex test'), \
                patch.object(c.subprocess, 'Popen', Process), \
                patch.object(c.CodexRunner, 'stop') as stop:
            runner = c.CodexRunner(timeout=1)
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run(self.root / 'runner', 'Review.', [], c.CALIBRATION_SCHEMA)
        stop.assert_called_once()
        self.assertFalse((self.root / 'runner/receipt.json').exists())


class ReleasingRunner(OfflineRunner):
    def __init__(self, packet, **kwargs):
        super().__init__(packet, **kwargs)
        self.released = []

    def release(self, session):
        self.released.append(session)


class ShortCalibrationRunner(OfflineRunner):
    """Answers batch-001's calibration without its cases a given number of times, then completely."""
    def __init__(self, packet, short=1):
        super().__init__(packet)
        self.short, self.released = short, []

    def release(self, session):
        self.released.append(session)

    def run(self, directory, prompt, images, schema, *, resume=None):
        if directory.name == 'calibration' and directory.parent.name == 'batch-001' and self.short:
            self.short -= 1
            self.calls.append((directory, resume, images))
            result = {'cases': []}
            directory.mkdir(parents=True)
            c.write_json(directory / 'schema.json', schema)
            c.write_json(directory / 'response.json', result)
            (directory / 'prompt.txt').write_text(prompt)
            (directory / 'events.jsonl').write_text('offline protocol stand-in\n')
            receipt = {'thread': resume, 'resumeThread': resume, 'promptSha256': c.digest(prompt.encode()),
                       'schemaSha256': c.digest(c.encoded(schema)), 'images': [], 'responseSha256': c.digest(c.encoded(result)),
                       'eventsSha256': c.digest((directory / 'events.jsonl').read_bytes())}
            c.write_json(directory / 'receipt.json', receipt)
            return result, receipt
        return super().run(directory, prompt, images, schema, resume=resume)


class CalibrationAnswerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home, self.packet = fixture(self.root)
        self.calibration = c.read_json(self.packet / 'calibration/packet.json')

    def test_the_schema_asks_for_each_case_once(self):
        schema = c.calibration_schema(self.calibration)
        cases = schema['properties']['cases']
        self.assertEqual((1, 1, ['case']), (cases['minItems'], cases['maxItems'], cases['items']['properties']['id']['enum']))
        with self.assertRaisesRegex(ValueError, 'wrong number of items'):
            c.validate_calibration({'cases': []}, self.calibration)

    def test_a_short_answer_is_asked_again_once_in_the_same_session(self):
        runner = ShortCalibrationRunner(self.packet, short=1)
        self.assertEqual(0, c.coordinate(self.packet, self.root / 'review', self.home, batch_size=2, runner=runner,
                                         harness=OfflineHarness(), printer=lambda *_: None))
        job = self.root / 'review/jobs/batch-001'
        self.assertTrue((job / 'invalid-calibration/receipt.json').exists())
        asked = [resume for directory, resume, _ in runner.calls
                 if (directory.parent.name, directory.name) == ('batch-001', 'calibration')]
        self.assertEqual(2, len(asked))
        self.assertEqual(1, len(set(asked)), 'both answers come from the reviewer that made the assessment')

    def test_a_second_short_answer_leaves_the_job_incomplete_and_its_session_kept(self):
        runner = ShortCalibrationRunner(self.packet, short=2)
        self.assertEqual(2, c.coordinate(self.packet, self.root / 'review', self.home, batch_size=2, runner=runner,
                                         harness=OfflineHarness(), printer=lambda *_: None))
        self.assertTrue(any('batch-001' in b and 'wrong number of items' in b
                            for b in c.read_json(self.root / 'review/report.json')['blockers']))
        self.assertNotIn('offline-batch-001', runner.released)


class SessionReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home, self.packet = fixture(self.root)

    def test_sessions_are_released_after_calibration_and_consolidation(self):
        runner = ReleasingRunner(self.packet, candidates=True)
        c.coordinate(self.packet, self.root / 'review', self.home, batch_size=2, runner=runner,
                     harness=OfflineHarness(), printer=lambda *_: None)
        calibrated = [resume for directory, resume, _ in runner.calls if directory.name == 'calibration']
        self.assertTrue(calibrated)
        self.assertEqual(sorted(calibrated + ['offline-review']), sorted(runner.released),
                         'every calibrated session and the consolidation session, nothing else')

    def test_a_session_whose_calibration_failed_is_kept_for_resume(self):
        runner = ReleasingRunner(self.packet, fail=lambda d: d.name == 'calibration' and d.parent.name == 'batch-001')
        c.coordinate(self.packet, self.root / 'review', self.home, batch_size=2, runner=runner,
                     harness=OfflineHarness(), printer=lambda *_: None)
        self.assertNotIn('offline-batch-001', runner.released)
        self.assertIn('offline-batch-002', runner.released)

def claude_events(session, response, *, tools=('StructuredOutput',), blocks=None, result=None):
    """The stream-json lines Claude Code prints for one print-mode turn."""
    blocks = blocks if blocks is not None else [
        {'type': 'thinking', 'thinking': ''},
        {'type': 'tool_use', 'name': 'StructuredOutput', 'input': response}]
    return [{'type': 'system', 'subtype': 'init', 'session_id': session, 'tools': list(tools), 'mcp_servers': []},
            {'type': 'assistant', 'message': {'content': blocks}},
            {'type': 'rate_limit_event'},
            result or {'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': session,
                       'structured_output': response}]


class ClaudeRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _, self.packet = fixture(self.root)
        png = b'\x89PNG\r\n\x1a\n' + b'offline image'
        (self.packet / 'state-0.png').write_bytes(png)
        self.response = {'cases': [{'id': 'one', 'issue': False, 'reason': 'Visible control.'}]}

    def runner(self, launches, events=None, signed_in=True, returncode=0):
        response = self.response

        class Process:
            def __init__(self, command, **kwargs):
                launches.append((command, kwargs))
                self.command, self.kwargs, self.returncode = command, kwargs, returncode

            def communicate(self, stdin, timeout):
                self.stdin = stdin
                launches[-1] = (self.command, {**self.kwargs, 'input': stdin})
                session = self.command[self.command.index('--resume' if '--resume' in self.command else '--session-id') + 1]
                for event in (events(session) if events else claude_events(session, response)):
                    self.kwargs['stdout'].write(json.dumps(event) + '\n')

        status = subprocess.CompletedProcess([], int(not signed_in), stdout=json.dumps({'loggedIn': signed_in}))
        patches = [patch.object(c.shutil, 'which', return_value='/mock/claude'),
                   patch.object(c.subprocess, 'check_output', return_value='2.1.330 (Claude Code)'),
                   patch.object(c.subprocess, 'run', return_value=status),
                   patch.object(c.subprocess, 'Popen', Process),
                   patch.object(c.tempfile, 'gettempdir', return_value=str(self.root / 'tmp')),
                   patch.object(c.ClaudeRunner, 'scratch_roots', (self.root / 'systmp',)),
                   patch.dict(c.os.environ, {'CEDAR_PRIVATE_TOKEN': 'must-not-reach-reviewer',
                                             'CLAUDE_CODE_OAUTH_TOKEN': 'reviewer-login',
                                             'CLAUDE_CONFIG_DIR': str(self.root / 'config')})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return c.ClaudeRunner()

    def test_delivers_labelled_images_on_stdin_without_tools_settings_or_credentials(self):
        launches = []
        images = [self.packet / 'state-0.png']
        result, receipt = self.runner(launches).run(self.root / 'runner', 'Review the image.', images,
                                                    c.CALIBRATION_SCHEMA)
        self.assertEqual(result, self.response)
        self.assertEqual(c.read_json(self.root / 'runner/response.json'), self.response)
        command, options = launches[0]
        for flag in ('--print', '--safe-mode', '--strict-mcp-config', '--disable-slash-commands', '--no-chrome'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertEqual(command[command.index('--setting-sources') + 1], '')
        self.assertEqual(json.loads(command[command.index('--json-schema') + 1]), c.CALIBRATION_SCHEMA)
        self.assertEqual(receipt['thread'], command[command.index('--session-id') + 1])
        self.assertEqual(receipt['runner'], '2.1.330 (Claude Code)')
        self.assertNotIn('CEDAR_PRIVATE_TOKEN', options['env'])
        self.assertEqual('reviewer-login', options['env']['CLAUDE_CODE_OAUTH_TOKEN'])
        self.assertEqual(self.root / 'tmp/cedar-ui-reviewer' / receipt['thread'], Path(options['cwd']))
        content = json.loads(options['input'])['message']['content']
        self.assertEqual(['text', 'image', 'text'], [block['type'] for block in content])
        self.assertEqual('Image 1: state-0.png', content[0]['text'])
        self.assertEqual(('image/png', images[0].read_bytes()),
                         (content[1]['source']['media_type'], c.base64.b64decode(content[1]['source']['data'])))
        self.assertEqual('Review the image.', content[2]['text'])

    def test_calibration_resumes_the_reviewer_in_its_own_directory(self):
        launches = []
        runner = self.runner(launches)
        _, first = runner.run(self.root / 'assessment', 'Review.', [], c.CALIBRATION_SCHEMA)
        _, second = runner.run(self.root / 'calibration', 'Calibrate.', [], c.CALIBRATION_SCHEMA,
                               resume=first['thread'])
        self.assertEqual((first['thread'], first['thread']), (second['thread'], second['resumeThread']))
        self.assertEqual(launches[0][1]['cwd'], launches[1][1]['cwd'])
        self.assertNotIn('--session-id', launches[1][0])

    def test_release_deletes_only_the_finished_sessions_files(self):
        runner = self.runner([])
        _, receipt = runner.run(self.root / 'assessment', 'Review.', [], c.CALIBRATION_SCHEMA)
        session, other = receipt['thread'], '0f0e0d0c-0b0a-4909-8807-060504030201'
        name = lambda id_: '-private-tmp-cedar-ui-reviewer-' + id_
        kept = [self.root / 'config/projects' / name(other), self.root / 'config/projects/-Users-someone-project',
                self.root / 'systmp' / f'claude-{c.os.getuid()}' / name(other), runner.workspace(other)]
        removed = [self.root / 'config/projects' / name(session),
                   self.root / 'systmp' / f'claude-{c.os.getuid()}' / name(session) / session / 'images',
                   self.root / 'tmp' / f'claude-{c.os.getuid()}' / name(session)]
        for path in kept + removed:
            path.mkdir(parents=True, exist_ok=True)
            (path / 'kept.txt').write_text('file')
        self.assertTrue(runner.workspace(session).is_dir())
        runner.release(session)
        self.assertEqual([], [p for p in removed + [runner.workspace(session)] if p.exists()])
        self.assertEqual(kept, [p for p in kept if (p / 'kept.txt').exists()])
        self.assertEqual((self.root / 'assessment/receipt.json').exists(), True, 'the review keeps its own record')
        with self.assertRaisesRegex(ValueError, 'session identifier'):
            runner.release('*')

    def test_refuses_tools_failed_turns_and_missing_answers(self):
        cases = {
            'unexpected tool': lambda s: claude_events(s, self.response, blocks=[
                {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/etc/hosts'}}]),
            'tools beyond its structured answer': lambda s: claude_events(s, self.response, tools=('StructuredOutput', 'Read')),
            'Not logged in': lambda s: claude_events(s, self.response, result={
                'type': 'result', 'subtype': 'success', 'is_error': True, 'result': 'Not logged in', 'session_id': s}),
            'no structured answer': lambda s: claude_events(s, self.response, result={
                'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': s, 'result': '{}'}),
            'session identity': lambda s: claude_events(s, self.response, result={
                'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': 'another',
                'structured_output': self.response}),
        }
        for message, events in cases.items():
            with self.subTest(message=message):
                directory = self.root / message.replace(' ', '-')
                with self.assertRaisesRegex(ValueError, message):
                    self.runner([], events).run(directory, 'Review.', [], c.CALIBRATION_SCHEMA)
                self.assertFalse((directory / 'receipt.json').exists())

    def test_signed_out_reviewer_is_refused_before_a_review_directory_exists(self):
        with self.assertRaisesRegex(ValueError, 'not signed in'):
            self.runner([], signed_in=False)
        home, packet = fixture(self.root / 'second')
        output = self.root / 'review'
        with patch.object(c, 'REVIEWERS', {'claude': lambda *_: (_ for _ in ()).throw(ValueError('Claude Code is not signed in'))}):
            with self.assertRaisesRegex(ValueError, 'not signed in'):
                c.coordinate(packet, output, home, reviewer='claude', harness=OfflineHarness(), printer=lambda *_: None)
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, 'Unknown reviewer'):
            c.coordinate(packet, output, home, reviewer='gemini', printer=lambda *_: None)

    def test_too_many_images_split_verification_and_refuse_a_primary_batch(self):
        candidates = [{'id': 'a', 'job': 'batch-001'}, {'id': 'b', 'job': 'batch-001'}]
        def request(rows):
            return 'evidence', [row['id'] + str(i) + '.png' for row in rows for i in range(3)]
        jobs = c.bounded_verification_jobs([{'id': 'batch-001'}], candidates, request, image_limit=4)
        self.assertEqual([['a'], ['b']], [[f['id'] for f in j['candidates']] for j in jobs])
        self.assertTrue(all(j['error'] is None for j in jobs))
        single = c.bounded_verification_jobs([{'id': 'batch-001'}], candidates[:1], request, image_limit=2)
        self.assertIn('exceeds 2 images', single[0]['error'])

        class Narrow(OfflineRunner):
            image_limit = 1
        home, packet = fixture(self.root / 'narrow')
        code = c.coordinate(packet, self.root / 'narrow-review', home, batch_size=2, runner=Narrow(packet),
                            harness=OfflineHarness(), printer=lambda *_: None)
        self.assertEqual(2, code)
        self.assertTrue(any('smaller --batch-size' in b for b in c.read_json(self.root / 'narrow-review/report.json')['blockers']))


class SharedEvidenceTest(unittest.TestCase):
    def test_repeated_related_evidence_roundtrips_without_changing_order_or_values(self):
        row = {'label': 'Mélanoma — full authored name ' * 20,
               'x': 1.125, 'expanded': None, 'disabled': False,
               'pointerTarget': {'x': 11.5, 'y': 24, 'belongsToControl': True}}
        original = {'states': [{'id': str(i), 'rows': [copy.deepcopy(row), {'label': row['label']}],
                                'accessibility': 'button: complete source and release name\n' * 20}
                               for i in range(20)]}
        before = copy.deepcopy(original)
        encoded = c.shared_evidence_json(original)
        packet = json.loads(encoded)
        def decode(value):
            if isinstance(value, dict) and set(value) == {'$sharedEvidenceValue'}:
                return decode(packet['sharedValues'][value['$sharedEvidenceValue']])
            if isinstance(value, dict):
                return {key: decode(child) for key, child in value.items()}
            if isinstance(value, list):
                return [decode(child) for child in value]
            return value
        self.assertEqual(decode(packet['evidence']), original)
        self.assertEqual(original, before)
        self.assertLess(len(encoded), len(json.dumps(original)) / 2)

    def test_reference_like_input_cannot_be_silently_reinterpreted(self):
        with self.assertRaisesRegex(ValueError, 'reserved'):
            c.shared_evidence_json({'original': {'$sharedEvidenceValue': 0}})



if __name__ == '__main__':
    unittest.main()

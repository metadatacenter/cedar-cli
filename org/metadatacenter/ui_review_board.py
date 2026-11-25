"""A local board for triaging UI review results and recording a person's answers.

The board reads the reports that `cedarcli test ui-review` and `cedarcli test ui-review-coordinate`
retain under `.cedar/reports` and never writes to them. A person's answers, and the design
decisions put to them, live in a store of their own beside the reports, so a sealed review stays
exactly as its reviewers left it. The server binds the loopback interface only.

Every review identifies its findings afresh, so the same problem reported by two runs arrives under
two identifiers. An answer therefore belongs to an issue rather than to a finding. A finding joins
an issue when it is answered or when the person links it to one, and the board suggests the issues
an unlinked finding resembles. A design decision and a captured state keep their own identifiers,
which are already stable.
"""
from collections import Counter
import json
import math
import mimetypes
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = Path(__file__).resolve().parents[2] / 'assets' / 'ui-review-board' / 'index.html'
STATUSES = ('approved', 'needs-fix', 'deferred', 'option')
IMAGE_TYPES = ('.png', '.jpg', '.jpeg', '.webp')
DEFAULT_PORT = 47831
ANSWERS = 'answers.json'
_ID = re.compile(r'^[\w./:@-]{1,300}$')
# A suggestion is offered from SUGGEST and called likely from LIKELY. Both were set by scoring every
# pair of findings across six retained Designer runs: above 0.6 the pairs were the same problem.
SUGGEST, LIKELY = 0.5, 0.65
_STOP = frozenset('''a an the of to in on at and or for with is are its it as by from be not than that
this into below above beside sits stays when while which their there these those was were has have
but so if then same only one two each other more less no can may should would could does do'''.split())


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def board_dir(reports):
    return Path(reports) / 'ui-review-board'


def _updated(path):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(Path(path).stat().st_mtime))


def _optional(path):
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def designer_area(state_ids):
    """The part of the Designer a finding concerns, from the states it names."""
    if any('term-picker' in state for state in state_ids):
        return 'Term picker'
    # A Designer state with Preview open names "preview" too; only findings on Preview alone belong to CEE.
    if state_ids and all('preview' in state for state in state_ids):
        return 'CEE Preview'
    return 'Designer'


def state_group(state_id):
    """A catalog family for browsing captured states; only a navigation aid."""
    for prefix, name in (('term-picker', 'Term picker'), ('type-', 'Field types'), ('required', 'Required fields'),
                         ('server-findings', 'Save and recovery'), ('draft', 'Save and recovery'),
                         ('save', 'Save and recovery'), ('saved', 'Save and recovery'), ('reopened', 'Save and recovery'),
                         ('width-', 'Widths and panels'), ('both-panels', 'Widths and panels'),
                         ('phone-', 'Widths and panels'), ('configuration-end', 'Widths and panels')):
        if state_id.startswith(prefix):
            return name
    return 'Creation controls' if 'creation' in state_id else 'Other states'


def _app_from_path(relative):
    """Captures made before the catalog covered several applications record none; they were all Designer."""
    for app in ('metadata-editor', 'workspace', 'openview', 'designer'):
        if app in relative:
            return app
    return 'designer'


def discover_runs(reports):
    """Every retained review the board can show, newest first."""
    reports = Path(reports)
    runs = []
    for report_path in sorted((reports / 'ui-review-coordinator').glob('*/report.json')):
        report = _optional(report_path)
        if not isinstance(report, dict) or 'findings' not in report:
            continue
        directory = report_path.parent
        runner = _optional(directory / 'runner.json') or {}
        capture = _optional(directory / 'packet' / 'capture.json') or {}
        groups = report.get('findings', []) + report.get('designDecisions', []) + report.get('dismissed', [])
        runs.append({'id': 'coordinated/' + directory.name, 'kind': 'coordinated', 'name': directory.name,
                     'app': report.get('app', 'designer'), 'profile': capture.get('profile', ''),
                     'verdict': report.get('verdict', ''), 'coverage': report.get('coverage', {}),
                     'runner': runner.get('runner', ''), 'model': runner.get('requestedModel', ''),
                     'findings': len(groups), 'confirmed': len(report.get('findings', [])),
                     'updated': _updated(report_path)})
    for review_path in sorted((reports / 'ui-review').glob('**/review.json')):
        directory = review_path.parent
        review, capture = _optional(review_path), _optional(directory / 'capture.json')
        if not isinstance(review, dict) or not isinstance(capture, dict):
            continue
        relative = directory.relative_to(reports / 'ui-review').as_posix()
        runs.append({'id': 'single/' + relative, 'kind': 'single', 'name': relative,
                     'app': capture.get('app') or _app_from_path(relative), 'profile': capture.get('profile', ''),
                     'verdict': '', 'coverage': {'captured': len(capture.get('states', [])),
                                                 'assessed': len(review.get('inspectedStates', []))},
                     'runner': review.get('reviewer', 'single reviewer'), 'model': '',
                     'findings': len(review.get('findings', [])), 'updated': _updated(review_path)})
    return sorted(runs, key=lambda run: run['updated'], reverse=True)


def _run_directory(reports, run_id):
    kind, _, name = run_id.partition('/')
    base = {'coordinated': Path(reports) / 'ui-review-coordinator', 'single': Path(reports) / 'ui-review'}.get(kind)
    if base is None or not name:
        raise ValueError('Unknown review: ' + run_id)
    directory = (base / name).resolve()
    if base.resolve() not in directory.parents:
        raise ValueError('Unknown review: ' + run_id)
    return kind, directory


def image_url(reports, path):
    return '/image?path=' + Path(path).resolve().relative_to(Path(reports).resolve()).as_posix()


def _captions(*findings):
    """What each cited image shows, as reviewers caption it; the first caption of an image wins."""
    captions = {}
    for finding in findings:
        for caption in finding.get('evidenceCaptions') or []:
            if isinstance(caption, dict) and isinstance(caption.get('file'), str) and isinstance(caption.get('caption'), str):
                captions.setdefault(caption['file'], caption['caption'])
    return captions


def _images(reports, root, names, captions=None):
    # Later single reviews cite evidence as {path, sha256}; earlier ones cite the file name alone.
    names = [name.get('path') if isinstance(name, dict) else name for name in names]
    captions = captions or {}
    return [{'src': image_url(reports, root / name), 'label': name, 'caption': captions.get(name, '')}
            for name in names if isinstance(name, str) and Path(name).suffix.lower() in IMAGE_TYPES and (root / name).is_file()]


def _occurrence(reports, root, finding):
    verification = finding.get('verification') or {}
    return {'id': finding['id'], 'title': finding['title'], 'observed': finding.get('observed', ''),
            'expected': finding.get('expected', ''), 'impact': finding.get('impact', ''),
            'steps': finding.get('steps', []), 'states': finding.get('stateIds', []),
            'severity': finding.get('severity', ''), 'kind': finding.get('kind', ''),
            'verifier': verification.get('decision', 'unverified'), 'verifierReason': verification.get('reason', ''),
            'calibrated': finding.get('calibrated'), 'images': _images(reports, root, finding.get('evidence', []), _captions(finding))}


def run_contents(reports, run_id):
    """A review's findings, as items a person can answer, and its per-state assessments."""
    kind, directory = _run_directory(reports, run_id)
    if kind == 'coordinated':
        report = read_json(directory / 'report.json')
        packet = directory / 'packet'
        items = []
        for section, groups in (('findings', report.get('findings', [])), ('designDecisions', report.get('designDecisions', [])),
                                ('dismissed', report.get('dismissed', []))):
            for group in groups:
                occurrences = [_occurrence(reports, packet, o) for o in group.get('occurrences', [group])]
                status = {'findings': 'confirmed', 'dismissed': 'dismissed'}.get(section) or (
                    'design' if group.get('kind') == 'design-proposal' else 'uncertain')
                if section == 'designDecisions' and not group.get('verification'):
                    status = 'not-verified'
                items.append({'id': run_id + '/' + group['id'], 'title': re.sub(r'^\d+[.)]\s*', '', group['title']),
                              'summary': group.get('observed', ''), 'status': status,
                              'target': group.get('target', ''), 'category': group.get('category', ''),
                              'severity': group.get('severity', ''),
                              'provisional': not group.get('calibrated', False) and section != 'dismissed',
                              'area': designer_area(group.get('stateIds', [])) if report.get('app', 'designer') == 'designer' else report.get('app', ''),
                              'states': group.get('stateIds', []), 'occurrences': occurrences,
                              'images': _images(reports, packet, group.get('evidence', []),
                                                _captions(group, *group.get('occurrences', [])))})
        coverage = _optional(directory / 'coverage.json') or {'states': []}
        states = []
        for row in coverage.get('states', []):
            assessment = row.get('assessment') or {}
            aspects = assessment.get('aspects') or {}
            raised = [name for name, value in aspects.items() if value.get('status') in ('concern', 'uncertain')]
            states.append({'id': run_id + '/state/' + row['id'], 'title': row['id'], 'group': state_group(row['id']),
                           'selfIdentified': True,
                           'summary': assessment.get('visualObservation', ''), 'status': 'raised' if raised else 'passed',
                           'raised': raised, 'aspects': aspects, 'calibrated': row.get('calibrated'),
                           'images': _images(reports, packet, list(row.get('images', {})))})
        app = report.get('app', 'designer')
        _suggest(answers(reports), items, app)
        return {'run': run_id, 'app': app, 'items': items, 'states': states, 'blockers': report.get('blockers', [])}
    review = read_json(directory / 'review.json')
    capture = _optional(directory / 'capture.json') or {}
    app = capture.get('app') or _app_from_path(run_id.partition('/')[2])
    items = [{'id': run_id + '/' + f['id'], 'title': f['title'], 'summary': f.get('observed', ''),
              'target': f.get('target', ''), 'category': f.get('category', ''),
              'status': 'not-verified', 'severity': str(f.get('severity', '')), 'provisional': False,
              'area': directory.name, 'states': [], 'images': _images(reports, directory, f.get('evidence', []), _captions(f)),
              'occurrences': [{**_occurrence(reports, directory, {**f, 'stateIds': []}), 'verifier': 'unverified'}]}
             for f in review.get('findings', [])]
    _suggest(answers(reports), items, app)
    return {'run': run_id, 'app': app, 'items': items, 'states': [], 'blockers': []}


def decision_problems(reports, decision):
    """What keeps a decision from being put to a person, in the form every decision takes.

    A decision asks one question, says how a user reaches the situation it concerns and what is
    going on there, and walks through the screens that show it, each with a title and a caption
    saying what that screen shows. It offers at least two options, at most one of them recommended.
    """
    def text(value):
        return isinstance(value, str) and value.strip()
    problems = []
    if not text(decision.get('summary')) or not decision['summary'].strip().endswith('?'):
        problems.append('no question: the summary must ask one, ending in a question mark')
    if not text(decision.get('background')):
        problems.append('no background saying how a user reaches this and what is going on')
    steps = decision.get('steps')
    if not isinstance(steps, list) or not steps:
        problems.append('no screens: steps must list each screenshot with a title and a caption')
    else:
        for number, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                problems.append(f'screen {number} is not a step')
                continue
            if not (isinstance(step.get('path'), str) and (Path(reports) / step['path']).is_file()):
                problems.append(f'screen {number} has no screenshot at {step.get("path")!r}')
            if not text(step.get('title')):
                problems.append(f'screen {number} has no title')
            if not text(step.get('caption')):
                problems.append(f'screen {number} has no caption saying what it shows')
    options = decision.get('options') if isinstance(decision.get('options'), list) else []
    if len([o for o in options if isinstance(o, dict) and text(o.get('label'))]) < 2:
        problems.append('fewer than two options')
    if len([o for o in options if isinstance(o, dict) and o.get('recommended')]) > 1:
        problems.append('more than one recommended option')
    return problems


def decisions(reports):
    """Design decisions put to the person, with their options; kept in the board's own store.

    A decision walks through its screens as steps, each a screenshot with a title and a caption
    saying what the screen shows. A plain list of evidence paths is still read, captioned by name,
    and reported as a problem, like any other departure from that form.
    """
    value = _optional(board_dir(reports) / 'decisions.json') or {'decisions': []}
    out = []
    for decision in value.get('decisions', []):
        steps = decision.get('steps') or [{'path': path, 'title': Path(path).stem} for path in decision.get('evidence', [])]
        steps = [step for step in steps if isinstance(step, dict)]
        images = [{'src': image_url(reports, Path(reports) / step['path']), 'label': step.get('title') or Path(step['path']).stem,
                   'caption': step.get('caption', '')}
                  for step in steps if isinstance(step.get('path'), str) and (Path(reports) / step['path']).is_file()]
        out.append({**decision, 'id': 'decision/' + decision['slug'], 'images': images, 'selfIdentified': True,
                    'problems': decision_problems(reports, decision)})
    return out


def answers(reports):
    """The person's answers: issues with their answer, and the findings linked to each issue."""
    stored = _optional(board_dir(reports) / ANSWERS) or {}
    return {'issues': stored.get('issues', {}), 'links': stored.get('links', {})}


def _words(text):
    words = []
    for word in re.findall(r'[a-z0-9]+', text.lower()):
        if word in _STOP or len(word) < 2:
            continue
        words.append(word[:-1] if len(word) > 4 and word.endswith('s') else word)
    return words


def _profile(finding):
    """What a finding is compared by: its wording, weighting title and target over the observation."""
    text = ' '.join([finding.get('title', '')] * 2 + [finding.get('target', '')] * 2 + [finding.get('summary', '')])
    return Counter(_words(text)), set(finding.get('states', [])), finding.get('category', '')


def _suggest(store, items, app):
    """Give each unlinked finding the answered issues it most resembles, best first.

    Wording is compared by cosine similarity and states by Jaccard overlap, each weighted by inverse
    frequency over this review and the linked findings, since a word or state that most findings
    share says little about which problem a finding describes.
    """
    links = {key: link for key, link in store['links'].items() if link.get('app') == app}
    for item in items:
        item['issue'] = store['links'].get(item['id'], {}).get('issue')
        item['matches'] = []
    candidates = [item for item in items if not item['issue']]
    if not links or not candidates:
        return
    profiles = {key: _profile(link) for key, link in links.items()}
    profiles.update({item['id']: _profile(item) for item in items})
    total = len(profiles)
    words = Counter(word for counts, _, _ in profiles.values() for word in counts)
    states = Counter(state for _, ids, _ in profiles.values() for state in ids)

    def vector(counts):
        weighted = {word: count * math.log(1 + total / words[word]) for word, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in weighted.values())) or 1
        return {word: value / norm for word, value in weighted.items()}

    def overlap(first, second):
        weight = lambda ids: sum(math.log(1 + total / states[state]) for state in ids)
        union = weight(first | second)
        return weight(first & second) / union if union else 0

    vectors = {key: vector(counts) for key, (counts, _, _) in profiles.items()}
    for item in candidates:
        _, item_states, item_category = profiles[item['id']]
        best = {}
        for key, link in links.items():
            if key == item['id'] or link['issue'] not in store['issues']:
                continue
            _, link_states, link_category = profiles[key]
            text = sum(value * vectors[key].get(word, 0) for word, value in vectors[item['id']].items())
            score = 0.55 * text + 0.35 * overlap(item_states, link_states) + 0.10 * (item_category == link_category and bool(item_category))
            best[link['issue']] = max(score, best.get(link['issue'], 0))
        item['matches'] = [{'issue': issue, 'title': store['issues'][issue].get('title', issue), 'score': round(score, 2),
                            'likely': score >= LIKELY}
                           for issue, score in sorted(best.items(), key=lambda pair: -pair[1]) if score >= SUGGEST][:3]


def _locate(reports, item_id):
    """The answerable item an identifier names; refuses any identifier the board did not offer."""
    if item_id.startswith('decision/'):
        found = next((d for d in decisions(reports) if d['id'] == item_id), None)
        if found:
            return found, None
    else:
        run_id = item_id.rsplit('/', 1)[0]
        try:
            contents = run_contents(reports, run_id)
        except (OSError, ValueError, KeyError):
            contents = None
        found = next((i for i in contents['items'] if i['id'] == item_id), None) if contents else None
        if found:
            return found, contents['app']
        run_id = item_id.split('/state/', 1)[0]
        try:
            contents = run_contents(reports, run_id) if '/state/' in item_id else None
        except (OSError, ValueError, KeyError):
            contents = None
        found = next((s for s in contents['states'] if s['id'] == item_id), None) if contents else None
        if found:
            return found, contents['app']
    raise ValueError('No such item: ' + item_id)


def _snapshot(item, app, issue):
    """Enough of a linked finding to suggest its issue for findings that later runs report."""
    return {'issue': issue, 'app': app, 'title': item['title'], 'target': item.get('target', ''),
            'category': item.get('category', ''), 'summary': item.get('summary', ''),
            'states': item.get('states', []), 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}


def _mint(store, app):
    taken = [int(key.rsplit('-', 1)[1]) for key in store['issues']
             if key.startswith(app + '-') and key.rsplit('-', 1)[1].isdigit()]
    return f'{app}-{max(taken, default=0) + 1:04}'


def _drop_orphan(store, issue):
    if issue and not issue.startswith('decision/') and '/state/' not in issue and not any(
            link['issue'] == issue for link in store['links'].values()):
        store['issues'].pop(issue, None)


def record_answer(reports, body):
    """Store one answer, link, or clear, and return the whole store.

    An answer to an unlinked finding opens an issue for it. A link joins a finding to an issue
    already answered, so the finding shows that answer. A clear detaches a finding from its issue,
    and drops the issue once no finding is linked to it.
    """
    if not isinstance(body, dict) or not isinstance(body.get('id'), str) or not _ID.match(body['id']):
        raise ValueError('An answer needs a valid item identifier')
    item_id = body['id']
    item, app = _locate(reports, item_id)
    own = bool(item.get('selfIdentified'))
    store = answers(reports)
    current = item_id if own else store['links'].get(item_id, {}).get('issue')
    if body.get('clear'):
        if own:
            store['issues'].pop(item_id, None)
        else:
            store['links'].pop(item_id, None)
            _drop_orphan(store, current)
    elif 'link' in body:
        issue = body['link']
        if own or not isinstance(issue, str) or issue not in store['issues'] or issue.startswith('decision/') or '/state/' in issue:
            raise ValueError('A finding can be linked only to an issue another finding answers')
        store['links'][item_id] = _snapshot(item, app, issue)
        if current != issue:
            _drop_orphan(store, current)
    else:
        status = body.get('status')
        if status not in STATUSES:
            raise ValueError('Unknown answer: ' + str(status))
        option, note = body.get('option'), body.get('note') or ''
        if status == 'option' and not (isinstance(option, str) and option):
            raise ValueError('Choosing an option needs the option')
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError('A note must be text of at most 4000 characters')
        issue = current or _mint(store, app)
        if not own and item_id not in store['links']:
            store['links'][item_id] = _snapshot(item, app, issue)
        previous = store['issues'].get(issue, {})
        store['issues'][issue] = {'title': previous.get('title', item['title']), 'app': app or 'decision',
                                  'status': status, 'option': option if status == 'option' else None,
                                  'note': note, 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    write_json(board_dir(reports) / ANSWERS, store)
    return store


def make_handler(reports):
    reports = Path(reports).resolve()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, body, kind='application/json; charset=utf-8', cache='no-store'):
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def fail(self, code, message):
            self.respond(code, json.dumps({'error': message}).encode())

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == '/':
                    return self.respond(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
                if url.path == '/api/index':
                    return self.respond(200, json.dumps({'runs': discover_runs(reports), 'decisions': decisions(reports),
                                                         'answers': answers(reports)}).encode())
                if url.path == '/api/run':
                    return self.respond(200, json.dumps(run_contents(reports, query.get('id', [''])[0])).encode())
                if url.path == '/image':
                    target = (reports / query.get('path', [''])[0]).resolve()
                    if reports not in target.parents or target.suffix.lower() not in IMAGE_TYPES or not target.is_file():
                        return self.fail(404, 'No such image')
                    return self.respond(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or 'image/png',
                                        'private, max-age=3600')
            except (OSError, ValueError, KeyError) as error:
                return self.fail(400, str(error))
            self.fail(404, 'Not found')

        def do_POST(self):
            if urlparse(self.path).path != '/api/answer':
                return self.fail(404, 'Not found')
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).hostname not in ('127.0.0.1', 'localhost', '::1'):
                return self.fail(403, 'Answers are accepted from this board only')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length > 20000:
                    raise ValueError('Answer too large')
                stored = record_answer(reports, json.loads(self.rfile.read(length) or b'{}'))
            except (ValueError, TypeError) as error:
                return self.fail(400, str(error))
            self.respond(200, json.dumps(stored).encode())

        def log_message(self, *args):
            pass

    return Handler


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(reports, port=DEFAULT_PORT, printer=print):
    """Serve the board on 127.0.0.1 and, where available, ::1, until interrupted."""
    handler = make_handler(reports)
    primary = ThreadingHTTPServer(('127.0.0.1', port), handler)
    servers = [primary]
    try:
        secondary = _V6Server(('::1', primary.server_address[1]), handler)
        servers.append(secondary)
        threading.Thread(target=secondary.serve_forever, daemon=True).start()
    except OSError:
        pass
    printer(f'UI review board: http://localhost:{primary.server_address[1]}/ (Ctrl-C to stop). '
            f'Answers are kept in {board_dir(reports) / ANSWERS}.')
    try:
        primary.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers[1:]:
            server.shutdown()
        for server in servers:
            server.server_close()
    return 0


def plan(reports, printer=print):
    runs = discover_runs(reports)
    printer(f'{len(runs)} retained reviews under {Path(reports)}:')
    for run in runs:
        printer(f"  {run['id']}  {run['app']} {run['profile']}  {run['verdict'] or run['kind']}  "
                f"{run['findings']} findings  {run['updated']}")
    store = answers(reports)
    offered = decisions(reports)
    printer(f'{len(offered)} design decisions, {len(store["issues"])} answered issues '
            f'covering {len(store["links"])} linked findings, in {board_dir(reports)}.')
    incomplete = [decision for decision in offered if decision['problems']]
    for decision in incomplete:
        printer(f"  {decision['slug']} is not ready to decide: " + '; '.join(decision['problems']) + '.')
    return 1 if incomplete else 0

"""Application UI evidence and independent-review validation; never changes the product."""
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import signal
import time
import uuid

from org.metadatacenter.reactor_evidence import source_identity
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util
from org.metadatacenter.util.local_stack_lock import local_stack


def runtime_evidence(home):
    home = Path(home)
    runtime = json.loads((home / '.reactor/runtime.json').read_text())
    build = json.loads((home / '.reactor/builds' / (runtime['build'] + '.json')).read_text())
    sources = {name: {'recorded': record['source'], 'current': source_identity(home / name)}
               for name, record in build['repositories'].items()}
    stale = [name for name, value in sources.items()
             if not value['recorded'].get('sourceSha256')
             or value['recorded']['sourceSha256'] != value['current'].get('sourceSha256')]
    metadata_only = [name for name, value in sources.items()
                     if name not in stale and value['recorded'] != value['current']]
    return {'build': runtime['build'], 'packages': runtime['packages'], 'sources': sources,
            'stale': stale, 'current': not stale, 'metadataOnlyChanges': metadata_only}


def served_findings(home, evidence):
    """Why the frontends serve something other than the build the capture records.

    Each host's installed packages must carry the stamps of the sources the selected build names,
    every component copy it serves must be those packages' bytes, and a host that records its own
    build identity must name the selected revision and the tree it serves. The capture then compares
    what the browser receives with those trees, which together bind the reviewed interface to the
    selected build rather than to whatever happens to be staged.
    """
    from org.metadatacenter.smoke_gate import served_bundle_findings
    home = Path(home)
    try:
        repositories = json.loads((home / '.reactor/builds' / (evidence['build'] + '.json')).read_text())['repositories']
        if not isinstance(repositories, dict):
            raise TypeError('the build names no repositories')
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f'the selected build record cannot be read ({error})']
    revisions = {}
    for path, record in repositories.items():
        source = record.get('source') if isinstance(record, dict) else None
        if isinstance(source, dict) and isinstance(source.get('revision'), str):
            revisions[Path(path).parts[0]] = source['revision']
    return served_bundle_findings(home, revisions, repositories, evidence.get('packages') or {})[0]


def catalog(root, env=None):
    """The applications and browser profiles the review catalog defines; the catalog is the only list."""
    output = subprocess.run(
        ['node', '--input-type=module', '-e',
         "import {APPS, PROFILES} from './ui-review/catalog.mjs'; "
         "console.log(JSON.stringify({applications: APPS, profiles: Object.keys(PROFILES)}))"],
        cwd=root, env=env, check=True, capture_output=True, text=True).stdout
    return json.loads(output)


def _invoke(command, *, cwd, env):
    process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Give the collector's signal handler time to close the browser, remove its
        # registered fixtures and restore preferences before killing it.
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            print("UI review cleanup did not finish; inspect the run's session.json and account lock.")
        return 130


def run_coverage(*, captures, output=None):
    """Which declared components the captures render and show, and how much served script they run."""
    home = Path(Util.cedar_home).resolve()
    root = home / 'cedar-development/ops/e2e'
    environment = dict(invocation_environment())
    environment['CEDAR_HOME'] = str(home)
    caller = Path(environment.get('CEDAR_CLI_CALLER_DIR', Path.cwd()))
    def absolute(value):
        path = Path(value)
        return (path if path.is_absolute() else caller / path).resolve()
    directories = [absolute(capture) for capture in captures]
    for directory in directories:
        if not (directory / 'component-census.json').is_file():
            raise ValueError(f'{directory} holds no component census; capture it with the current harness')
    command = ['node', str(root / 'ui-review/ui-coverage.mjs'), *map(str, directories)]
    if output:
        command.append('--out=' + str(absolute(output)))
    return _invoke(command, cwd=root, env=environment)


def run_review(*, plan=False, output=None, review=None, seal_review=False, diagnostic=False, app="designer",
               profile="chromium"):
    home = Path(Util.cedar_home).resolve()
    root = home / 'cedar-development/ops/e2e'
    environment = dict(invocation_environment())
    known = catalog(root, environment)
    if app != 'all' and app not in known['applications']:
        raise ValueError("Unknown UI review application: " + app)
    if profile not in known['profiles']:
        raise ValueError("Unknown UI review browser profile: " + profile)
    caller = Path(environment.get('CEDAR_CLI_CALLER_DIR', Path.cwd()))
    def absolute(value):
        path = Path(value)
        return (path if path.is_absolute() else caller / path).resolve()
    if sum(bool(value) for value in (plan, review, seal_review)) > 1:
        raise ValueError('Choose one of --plan, --review or --seal-review')
    command = ['node', str(root / 'ui-review/run.mjs'), '--app=' + app]
    if plan:
        # The catalog depends on the profile: the touch profile has no text-size states.
        return _invoke(command + ['--plan', f'--profile={profile}'], cwd=root, env=environment)
    if seal_review:
        if output is None:
            raise ValueError('--seal-review requires --output naming the captured run')
        return _invoke(command + [f'--output={absolute(output)}', '--seal-review'],
                       cwd=root, env=environment)
    if review:
        if output is None:
            raise ValueError('--review requires --output naming the captured run')
        return _invoke(command + [f'--output={absolute(output)}', f'--review={absolute(review)}'],
                       cwd=root, env=environment)
    # A capture drives shared services and must not race a deployment, smoke or load run.
    # Check provenance after acquiring the lock, and retain it through collector cleanup.
    # The reactor may already hold this re-entrant lock when it requests its capture.
    with local_stack(home, report=lambda message: print(message, flush=True)):
        evidence = runtime_evidence(home)
        if not evidence['current'] and not diagnostic:
            raise ValueError('Frontend sources changed since the selected build: ' + ', '.join(evidence['stale']) +
                             '. Run cedarcli build frontends. --diagnostic may inspect the existing runtime, '
                             'but cannot produce an acceptance pass.')
        served = served_findings(home, evidence)
        if served:
            evidence = {**evidence, 'current': False, 'servedFindings': served}
            if not diagnostic:
                raise ValueError('The frontends do not serve the selected build: ' + '; '.join(served[:3]) +
                                 ('; and more' if len(served) > 3 else '') + '. Run cedarcli build frontends.')
        directory = absolute(output) if output else home / '.cedar/reports/ui-review' / str(uuid.uuid4())
        directory.mkdir(parents=True, exist_ok=False)
        (directory / 'provenance.json').write_text(json.dumps(evidence, indent=2) + '\n')
        return _invoke(command + [f'--output={directory}', '--capture', f'--profile={profile}'], cwd=root, env=environment)


# Repositories whose source content a capture reviews. A frontend build that changes any of them
# captures fresh evidence for a later independent review. The model library decides what the
# editors render and accept, so it counts as much as the applications do.
REVIEWED_SOURCES = ('cedar-embeddable-designer', 'cedar-template-designer', 'cedar-workspace',
                    'cedar-embeddable-editor', 'cedar-embeddable-term-picker', 'cedar-openview',
                    'cedar-design-tokens', 'cedar-model-typescript-library')
AUTOMATIC_PACKETS_KEPT = 5


def reviewed_sources(evidence):
    return {name: value['current'].get('sourceSha256') for name, value in sorted(evidence['sources'].items())
            if Path(name).parts[0] in REVIEWED_SOURCES}


def review_tooling_sha256(home):
    """One digest for the review tooling: every capture module, the catalog among them, and the
    lockfile that fixes the browser driver and the checks. A change to any of them can leave the
    previous packet unable to validate, so it calls for a new capture. None when it cannot be read."""
    root = Path(home) / 'cedar-development/ops/e2e'
    try:
        files = sorted(path for path in (root / 'ui-review').glob('*.mjs')
                       if not path.name.endswith(('.test.mjs', '.browser-test.mjs')))
        files.append(root / 'package-lock.json')
        digest = hashlib.sha256()
        for path in files:
            digest.update(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}\n'
                          .encode())
        return digest.hexdigest() if files[:-1] else None
    except OSError:
        return None


def review_inputs(home, evidence):
    """Everything a capture's evidence depends on: the reviewed sources, the component packages the
    selected build installed, and the review tooling."""
    return {'sources': reviewed_sources(evidence),
            'packages': dict(sorted((evidence.get('packages') or {}).items())),
            'tooling': review_tooling_sha256(home)}


def capture_after_build(home, *, printer=print):
    """Capture review evidence after a frontend build when anything the review shows changed.

    The capture never fails the build: an incomplete packet is reported, and the next build that
    reaches this step tries again, because only a complete capture records the inputs it covered.
    Every step is inside that guarantee, reading the previous record and pruning old packets included.
    """
    try:
        return _capture_after_build(Path(home), printer)
    except Exception as error:  # an optional step; nothing in it may fail the build
        printer(f'UI review capture could not run: {error}; the build is unaffected.')
        return 2


def _last_complete(latest):
    """The record of the last complete capture, or None when there is none or it cannot be read."""
    try:
        record = json.loads(latest.read_text())
        return record if isinstance(record, dict) else None
    except (OSError, ValueError):
        return None


def _capture_after_build(home, printer):
    root = home / '.cedar/reports/ui-review/automatic'
    latest = root / 'latest.json'
    evidence = runtime_evidence(home)
    inputs = review_inputs(home, evidence)
    previous = _last_complete(latest)
    # A record whose packet retention or a person removed no longer stands for evidence.
    if previous and previous.get('inputs') == inputs and Path(str(previous.get('packet'))).is_dir():
        printer('UI review capture skipped: nothing the review shows changed since the last complete capture.')
        return None
    root.mkdir(parents=True, exist_ok=True)
    directory = root / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + evidence['build'][:8])
    try:
        code = run_review(output=directory, app='all')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        printer('UI review capture could not run: ' + str(error))
        code = 2
    if code == 0:
        latest.write_text(json.dumps({'packet': str(directory), 'build': evidence['build'], 'inputs': inputs},
                                     indent=2) + '\n')
        printer(f'UI review evidence captured for independent review: {directory / "index.html"}')
    else:
        printer(f'UI review capture INCOMPLETE (exit {code}); the build is unaffected. Evidence: {directory}')
    # Pruning never removes the packet the record names, so incomplete captures cannot evict the
    # last complete one while the record still points at it.
    complete = _last_complete(latest)
    kept = Path(str(complete.get('packet'))).name if complete else None
    packets = sorted(path for path in root.iterdir() if path.is_dir())
    for stale in packets[:-AUTOMATIC_PACKETS_KEPT]:
        if stale.name != kept:
            shutil.rmtree(stale)
    return code

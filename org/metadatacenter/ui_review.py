"""Application UI evidence and independent-review validation; never changes the product."""
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
        return _invoke(command + ['--plan'], cwd=root, env=environment)
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
    evidence = runtime_evidence(home)
    if not evidence['current'] and not diagnostic:
        raise ValueError('Frontend sources changed since the selected build: ' + ', '.join(evidence['stale']) +
                         '. Run cedarcli build frontends. --diagnostic may inspect the existing runtime, '
                         'but cannot produce an acceptance pass.')
    directory = absolute(output) if output else home / '.cedar/reports/ui-review' / str(uuid.uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'provenance.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return _invoke(command + [f'--output={directory}', '--capture', f'--profile={profile}'], cwd=root, env=environment)


# Repositories whose source content a capture reviews. A frontend build that changes any of them
# captures fresh evidence for a later independent review.
REVIEWED_SOURCES = ('cedar-embeddable-designer', 'cedar-template-designer', 'cedar-workspace',
                    'cedar-embeddable-editor', 'cedar-embeddable-term-picker', 'cedar-openview',
                    'cedar-design-tokens')
AUTOMATIC_PACKETS_KEPT = 5


def reviewed_sources(evidence):
    return {name: value['current'].get('sourceSha256') for name, value in sorted(evidence['sources'].items())
            if Path(name).parts[0] in REVIEWED_SOURCES}


def capture_after_build(home, *, printer=print):
    """Capture review evidence after a frontend build when reviewed sources changed.

    The capture never fails the build: an incomplete packet is reported, and the next build that
    reaches this step tries again, because only a complete capture records the sources it covered.
    """
    home = Path(home)
    root = home / '.cedar/reports/ui-review/automatic'
    latest = root / 'latest.json'
    evidence = runtime_evidence(home)
    sources = reviewed_sources(evidence)
    if latest.exists() and json.loads(latest.read_text()).get('sources') == sources:
        printer('UI review capture skipped: no reviewed frontend source changed since the last complete capture.')
        return None
    root.mkdir(parents=True, exist_ok=True)
    directory = root / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + evidence['build'][:8])
    try:
        code = run_review(output=directory, app='all')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        printer('UI review capture could not run: ' + str(error))
        code = 2
    if code == 0:
        latest.write_text(json.dumps({'packet': str(directory), 'build': evidence['build'], 'sources': sources},
                                     indent=2) + '\n')
        printer(f'UI review evidence captured for independent review: {directory / "index.html"}')
    else:
        printer(f'UI review capture INCOMPLETE (exit {code}); the build is unaffected. Evidence: {directory}')
    packets = sorted(path for path in root.iterdir() if path.is_dir())
    for stale in packets[:-AUTOMATIC_PACKETS_KEPT]:
        shutil.rmtree(stale)
    return code

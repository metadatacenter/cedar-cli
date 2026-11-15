"""Designer UI evidence and independent-review validation; never changes the product."""
import json
from pathlib import Path
import subprocess
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
    stale = [name for name, value in sources.items() if value['recorded'] != value['current']]
    return {'build': runtime['build'], 'packages': runtime['packages'], 'sources': sources,
            'stale': stale, 'current': not stale}


def run_review(*, plan=False, output=None, review=None, seal_review=False, diagnostic=False):
    home = Path(Util.cedar_home).resolve()
    root = home / 'cedar-development/ops/e2e'
    environment = dict(invocation_environment())
    caller = Path(environment.get('CEDAR_CLI_CALLER_DIR', Path.cwd()))
    def absolute(value):
        path = Path(value)
        return (path if path.is_absolute() else caller / path).resolve()
    if sum(bool(value) for value in (plan, review, seal_review)) > 1:
        raise ValueError('Choose one of --plan, --review or --seal-review')
    command = ['node', str(root / 'ui-review/run.mjs')]
    if plan:
        return subprocess.run(command + ['--plan'], cwd=root, env=environment).returncode
    if seal_review:
        if output is None:
            raise ValueError('--seal-review requires --output naming the captured run')
        return subprocess.run(command + [f'--output={absolute(output)}', '--seal-review'],
                              cwd=root, env=environment).returncode
    if review:
        if output is None:
            raise ValueError('--review requires --output naming the captured run')
        return subprocess.run(command + [f'--output={absolute(output)}', f'--review={absolute(review)}'],
                              cwd=root, env=environment).returncode
    evidence = runtime_evidence(home)
    if not evidence['current'] and not diagnostic:
        raise ValueError('Frontend sources changed since the selected build: ' + ', '.join(evidence['stale']) +
                         '. Run cedarcli build frontends. --diagnostic may inspect the existing runtime, '
                         'but cannot produce an acceptance pass.')
    directory = absolute(output) if output else home / '.cedar/reports/ui-review' / str(uuid.uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'provenance.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return subprocess.run(command + [f'--output={directory}', '--capture'], cwd=root,
                          env=environment).returncode

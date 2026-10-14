"""Run the optional framework-consumer gate without changing working-tree pins."""
from pathlib import Path
import subprocess
import sys

from org.metadatacenter.frontend_inventory import inventory
from org.metadatacenter.util.InvocationContext import current_context, invocation_environment
from org.metadatacenter.util.NodeBuildCheck import require_build_node
from org.metadatacenter.util.Util import Util


def run_demos(package=None, *, if_changed=False):
    home = Path(Util.cedar_home).resolve()
    root = home / 'cedar-component-demo'
    require_build_node(root)
    _, config = inventory()
    consumers = [*config.get('additionalCeeConsumers', [])]
    consumers.extend({**row['ceeConsumer'], 'repository': row['repository']}
                     for row in config['frontends'] if row.get('ceeConsumer'))
    scratch = home / '.cedar/build-tmp'
    scratch.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(root / 'scripts/check-demos.py'),
               '--cee-source', str(home / 'cedar-embeddable-editor'),
               '--scratch', str(scratch),
               '--state', str(home / '.cedar/cee-demos' / ('candidate.json' if package else 'pins.json'))]
    if package:
        package = Path(package)
        if not package.is_absolute():
            package = Path(invocation_environment().get('CEDAR_CLI_CALLER_DIR', Path.cwd())) / package
        command.extend(['--cee-package', str(package.resolve())])
    if if_changed:
        command.append('--if-changed')
    for consumer in consumers:
        for key in ('manifest', 'lock'):
            command.extend(['--consumer-input', str(home / consumer['repository'] / consumer[key])])
    environment = dict(invocation_environment())
    environment['npm_config_cache'] = str(home / '.cedar/cee-demos/npm-cache')
    workers = str(current_context().settings.build_workers)
    environment.update({name: workers for name in ('CEDAR_TEST_WORKERS', 'NG_BUILD_MAX_WORKERS', 'VITEST_MAX_WORKERS')})
    return subprocess.run(command, env=environment).returncode


def check_selection(selection):
    home = Path(Util.cedar_home)
    digest = selection.get('cedar-embeddable-editor')
    if not digest:
        raise ValueError('Frontend build has no verified CEE package for the demo gate')
    code = run_demos(home / '.reactor/artifacts' / f'{digest}.tgz', if_changed=True)
    if code:
        raise SystemExit(code)

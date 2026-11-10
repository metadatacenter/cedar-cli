"""Run the same offline adoption checker used by the frontend CI jobs."""
import os
import json
from pathlib import Path
import subprocess
import sys

from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util


def check_design_tokens(repos=None, strict=False, json_output=False, show_all=False,
                        init_baseline=False, prune_baseline=False, sync_surfaces=False, surface_inventory=None,
                        source_consumers=(), runtime=False):
    home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
    if not home:
        print('CEDAR_HOME is not set', file=sys.stderr)
        return 2
    script = Path(home) / 'cedar-design-tokens/tools/check_adoption.py'
    if not script.is_file():
        print(f'{script} is missing; update the cedar-design-tokens checkout', file=sys.stderr)
        return 2
    command = [sys.executable, str(script), '--root', str(home)]
    for repo in repos or []:
        command += ['--repo', repo]
    for repo in source_consumers:
        command += ['--source-consumer', repo]
    for flag, enabled in (('--strict', strict), ('--json', json_output), ('--all', show_all),
                          ('--sync-surfaces', sync_surfaces), ('--init-baseline', init_baseline), ('--prune-baseline', prune_baseline)):
        if enabled:
            command.append(flag)
    if surface_inventory:
        command += ['--surface-inventory', str(_from_caller(surface_inventory))]
    if not runtime:
        return subprocess.run(command, env=invocation_environment()).returncode
    if init_baseline or prune_baseline or sync_surfaces or surface_inventory:
        print('--runtime is read-only and cannot be combined with write options', file=sys.stderr)
        return 2
    from org.metadatacenter.design_token_runtime import inspect_runtime, print_runtime
    completed = subprocess.run(command, env=invocation_environment(), capture_output=json_output, text=True)
    report = inspect_runtime(home, repos)
    if json_output:
        try:
            output = json.loads(completed.stdout)
        except ValueError:
            print(completed.stderr or 'Adoption checker returned invalid JSON', file=sys.stderr)
            return 2
        output['runtime'] = report
        print(json.dumps(output, indent=2))
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end='')
    else:
        print_runtime(report)
    return completed.returncode or int(strict and report['state'] != 'current')


def _from_caller(path):
    """Resolve a path the operator typed against the directory they typed it in.

    The wrapper runs the CLI from its own checkout, so this process's working directory is never
    the caller's, and the checker would otherwise write a relative file into cedar-cli.
    """
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    caller = invocation_environment().get('CEDAR_CLI_CALLER_DIR') or os.getcwd()
    return Path(caller) / candidate

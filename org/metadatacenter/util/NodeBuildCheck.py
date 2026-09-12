"""Check the selected build interpreter before installing or compiling anything."""
import re
import shutil
import subprocess
from pathlib import Path

from org.metadatacenter.release_support.policy import REQUIRED_NODE_VERSION
from org.metadatacenter.util.BuildSafety import BuildSafetyError
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util


def require_build_node(project, environment=None):
    environment = invocation_environment() if environment is None else environment
    project = Path(project)
    pin = project / '.nvmrc'
    try:
        required = pin.read_text().strip() if pin.is_file() else REQUIRED_NODE_VERSION
    except OSError as error:
        raise BuildSafetyError(f'Cannot read Node build pin {pin}: {error}') from error
    required = required.removeprefix('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+', required):
        raise BuildSafetyError(f'{pin} must pin an exact Node version; found {required!r}')
    executable = shutil.which('node', path=environment.get('PATH', ''))
    detected = 'not installed on the build PATH'
    if executable:
        try:
            result = subprocess.run([executable, '--version'], env=environment,
                                    capture_output=True, text=True, timeout=10, check=False)
            detected = result.stdout.strip() if result.returncode == 0 else 'could not run'
        except (OSError, subprocess.TimeoutExpired):
            detected = 'could not run'
        if detected.removeprefix('v') == required:
            return
    source = str(pin) if pin.is_file() else 'CEDAR release toolchain'
    raise BuildSafetyError(
        f'{project}: requires Node {required} ({source}); detected {detected}'
        f'{" at " + executable if executable else ""}. '
        f'Select Node {required} on the build PATH and rerun. '
        'Frontend dependency installation and compilation have not started for this task.')


def is_frontend_build(task):
    parameters = getattr(task, 'parameters', {})
    return any(parameters.get(key) is True for key in (
        'isolated_frontend_build', 'in_place_frontend_build'))


def require_plan_node(plan):
    """Check only enabled frontend tasks, before any task in a mixed plan starts."""
    checked = set()
    def visit(task):
        if is_frontend_build(task):
            project = Util.get_wd(task.repo)
            if project not in checked:
                require_build_node(project)
                checked.add(project)
        for child in task.tasks:
            visit(child)
    visit(plan)

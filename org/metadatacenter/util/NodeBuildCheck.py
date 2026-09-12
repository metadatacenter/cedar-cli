"""Check the selected build interpreter before installing or compiling anything."""
import re
import shutil
import subprocess
from pathlib import Path

from org.metadatacenter.release_support.policy import REQUIRED_NODE_VERSION
from org.metadatacenter.release_support.toolchain import node_24_remediation
from org.metadatacenter.util.BuildSafety import BuildSafetyError
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util


def _requirement(project, required, pin):
    """Who asks for this version, so the reader knows which file to argue with."""
    if pin.is_file():
        return f'{project.name} pins Node {required} in its .nvmrc'
    return f'{project.name} needs the Node {required} that the CEDAR release toolchain pins'


def _availability(executable, detected):
    """What the build PATH actually offers, phrased to follow 'but the build PATH'."""
    if executable is None:
        return 'has no node on it'
    if not detected:
        return f'has a node at {executable} that would not report its version'
    return f'offers {detected} at {executable}'


def _how_to_select(required):
    """Name the host's own way to reach this version, not a general instruction to find it."""
    if required == REQUIRED_NODE_VERSION.removeprefix('v'):
        return node_24_remediation()
    return (f'put a Node {required} bin directory first on PATH, '
            f'for example with nvm use {required}')


def _detected_version(executable, environment):
    if executable is None:
        return None
    try:
        result = subprocess.run([executable, '--version'], env=environment,
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


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
    detected = _detected_version(executable, environment)
    if detected and detected.removeprefix('v') == required:
        return
    raise BuildSafetyError(
        f'{_requirement(project, required, pin)}, '
        f'but the build PATH {_availability(executable, detected)}.\n'
        f'To select it:\n'
        f'    {_how_to_select(required)}\n'
        'Nothing has been installed or compiled for this task.')


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

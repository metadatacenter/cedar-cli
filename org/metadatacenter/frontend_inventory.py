"""Read the shared build, wiring and verification inventory owned by cedar-development."""
import importlib.util
from pathlib import Path
import shlex
from typing import NamedTuple
from org.metadatacenter.util.InvocationContext import invocation_environment


def inventory():
    home = Path(invocation_environment().get('CEDAR_HOME') or Path(__file__).resolve().parents[3])
    ops = home / 'cedar-development' / 'ops'
    spec = importlib.util.spec_from_file_location('cedar_frontend_inventory', ops / 'frontend_inventory.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module.load(ops / 'frontend-train.json')
    return module, config


def release_surfaces():
    module, config = inventory()
    return [row for row in module.surfaces(config) if row.get('release')]


class ReactorCheck(NamedTuple):
    """A surface's reactor commands, and how many of them leave its package built, if it says."""
    commands: list
    package_ready_after: int | None


def reactor_checks():
    """Each surface's commands in order: setup, its package phase if any, verify, then build."""
    module, config = inventory()
    checks = {}
    for row in module.surfaces(config):
        produce = [*row['setup'], *row.get('package', [])]
        commands = [*produce, *row['verify'], *([row['build']] if row['build'] else [])]
        checks[row['reactorName']] = ReactorCheck(
            [shlex.join(command) for command in commands],
            len(produce) if row.get('package') else None)
    return checks


def integration_inputs(repository):
    module, config = inventory()
    row = next(row for row in module.surfaces(config) if row['repository'] == repository)
    return row.get('integrationInputs', [])


class ReleaseSurfaces:
    """Load only when release planning needs the inventory, not during CLI startup."""
    def __iter__(self):
        return iter(release_surfaces())

    def __len__(self):
        return len(release_surfaces())

    def __getitem__(self, index):
        return release_surfaces()[index]

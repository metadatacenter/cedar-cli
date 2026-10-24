"""Require current-source CEDAR packages in every local frontend build plan."""
import json
import os
from pathlib import Path

from org.metadatacenter import reactor
from org.metadatacenter.build_scheduler import shell_tasks
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util


def internal_dependencies(task):
    root = Path(Util.get_wd(task.repo))
    if not (root / 'package.json').is_file():
        raise reactor.ReactorError(f'Local SNAPSHOT build requires source manifest {root / "package.json"}')
    names = set()
    for directory, children, files in os.walk(root):
        children[:] = sorted(n for n in children if n not in reactor.SKIPPED_DIRECTORIES)
        if 'package.json' not in files:
            continue
        manifest = Path(directory) / 'package.json'
        try:
            package = json.loads(manifest.read_text())
            for section in reactor.DEPENDENCY_SECTIONS:
                for key, value in package.get(section, {}).items():
                    name = reactor.dependency_name(key, value)
                    if name.startswith('cedar-') and name != reactor.unscoped(package.get('name', '')):
                        names.add(name)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise reactor.ReactorError(f'Cannot resolve local SNAPSHOT inputs from {manifest}: {error}') from error
    if 'npm run test:ci' in task.command_list:
        from org.metadatacenter.frontend_inventory import integration_inputs
        names.update(item['repository'] for item in integration_inputs(task.repo.name))
    return names


def prepare(plan):
    """Expand narrow development builds to include their transitive local package producers.

    Full plans already include all producers. Server payloads, Java, publication and release
    plans do not acquire local dependency resolution. There is no registry-fallback option.
    """
    tasks = [t for t in shell_tasks(plan) if t.parameters.get('isolated_frontend_build')]
    if not tasks:
        return
    producers = {r.name: r for r in GlobalContext.repos.get_list_all() if r.published_package_path}
    scheduled = {t.repo.name for t in tasks}
    plan.frontend_snapshot = True
    from org.metadatacenter.planner.BuildPlanner import BuildPlanner
    extra = Plan('Local SNAPSHOT dependencies')
    pending = list(tasks)
    while pending:
        task = pending.pop(0)
        for name in sorted(internal_dependencies(task)):
            if name not in producers:
                raise reactor.ReactorError(
                    f'{task.repo.name} requires {name}, but no local package producer is registered. '
                    'Register its source build; development builds never fall back to registry pins.')
            if name in scheduled:
                continue
            scheduled.add(name)
            added = Plan('Build current-source dependency')
            added.add_task('Build local SNAPSHOT', TaskType.BUILD, [producers[name]],
                           parameters={'force_frontend_build': True})
            BuildPlanner.verify_frontends(added, reuse=True)
            extra.tasks.extend(added.tasks)
            pending.extend(shell_tasks(added))
    # The scheduler derives the exact producer order, including nested manifests and test inputs.
    plan.tasks[:0] = extra.tasks

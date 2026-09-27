"""Bounded build scheduling; Maven owns its module graph, npm edges come from manifests.

Only isolated frontend tasks overlap. Other commands remain exclusive barriers in
plan order. Context copies retain this invocation's artifact selection and evidence.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import copy_context, ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import uuid

from rich.console import Console

from org.metadatacenter import reactor
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

from org.metadatacenter.util.InvocationContext import process_cancellation as _cancel
_timings = ContextVar('build_timings', default=None)


def record_timing(repo, command, started, code, stages=()):
    records = _timings.get()
    if records is not None:
        records.append({'repository': repo, 'command': command,
                        'seconds': round(time.monotonic() - started, 3), 'exitCode': code,
                        'stages': list(stages)})


def shell_tasks(plan):
    result = []
    def visit(node):
        if (getattr(node, 'task_type', None) == TaskType.SHELL
                and node.command_list != ['echo "Nothing to do"']):
            result.append(node)
        for child in node.tasks:
            visit(child)
    visit(plan)
    return result


def dependencies(tasks):
    """Read the same nested manifests as reactor.resolve, including npm aliases."""
    producers = {t.repo.name: i for i, t in enumerate(tasks)
                 if getattr(t.repo, 'published_package_path', None)}
    edges = {i: set() for i in range(len(tasks))}
    for i, task in enumerate(tasks):
        if not task.parameters.get('isolated_frontend_build'):
            # An in-place or Maven command is an exclusive barrier. Maven itself
            # schedules its dependency graph with -T, in one owning process.
            edges[i].update(range(i))
            for later in range(i + 1, len(tasks)):
                edges[later].add(i)
            continue
        source = Path(Util.get_wd(task.repo))
        for directory, children, files in os.walk(source):
            children[:] = [n for n in children if n not in reactor.SKIPPED_DIRECTORIES]
            if 'package.json' not in files:
                continue
            manifest = Path(directory) / 'package.json'
            package = json.loads(manifest.read_text())
            for section in reactor.DEPENDENCY_SECTIONS:
                for name, spec in package.get(section, {}).items():
                    if isinstance(spec, str) and spec.startswith('npm:'):
                        name = spec[4:].rsplit('@', 1)[0]
                    producer = producers.get(reactor.unscoped(name))
                    if producer is not None and producer != i:
                        edges[i].add(producer)
        if 'npm run test:ci' in task.command_list:
            from org.metadatacenter.frontend_inventory import integration_inputs
            for item in integration_inputs(task.repo.name):
                producer = producers.get(item['repository'])
                if producer is not None and producer != i:
                    edges[i].add(producer)
    # Reject cycles before any build changes an artifact.
    remaining = set(edges)
    while remaining:
        ready = {i for i in remaining if not edges[i] & remaining}
        if not ready:
            names = ', '.join(tasks[i].repo.name for i in sorted(remaining))
            raise reactor.ReactorError('Cyclic build dependencies: ' + names)
        remaining -= ready
    return edges


def run_graph(nodes, edges, run, jobs, *, fail_fast=True, on_status=lambda *_: None):
    """Drain running jobs on failure; never launch a failed producer's consumers."""
    remaining, running, results = set(range(len(nodes))), {}, {}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        try:
            while remaining or running:
                for i in sorted(remaining.copy()):
                    if any(dep in results and results[dep] != 0 for dep in edges[i]):
                        remaining.remove(i)
                        results[i] = 125
                        on_status(i, 'blocked')
                failed = any(code != 0 for code in results.values())
                if not (failed and fail_fast):
                    for i in sorted(remaining.copy()):
                        if len(running) >= jobs:
                            break
                        if not edges[i] <= results.keys():
                            continue
                        remaining.remove(i)
                        on_status(i, 'running')
                        running[pool.submit(copy_context().run, run, nodes[i])] = i
                if not running:
                    if remaining:
                        if failed and fail_fast:
                            for i in remaining:
                                results[i] = 125
                                on_status(i, 'blocked')
                            break
                        if any(any(d in results and results[d] != 0 for d in edges[i]) for i in remaining):
                            continue
                        raise RuntimeError('No runnable build tasks')
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    i = running.pop(future)
                    results[i] = future.result() or 0
                    on_status(i, 'passed' if results[i] == 0 else 'failed')
        except BaseException:
            event = _cancel.get()
            if event is not None:
                event.set()
            raise
    return results


class BuildProgress:
    """One thread-safe output stream with repository prefixes instead of shared bars."""
    def __init__(self, console, name):
        self.console, self.name = console, name

    def print(self, value, *args, **kwargs):
        if isinstance(value, str):
            value = f'[{self.name}] {value}'
            kwargs.update(soft_wrap=True, highlight=False)
        self.console.print(value, *args, **kwargs)

    def update(self, *args, **kwargs):
        pass


def execute(plan, jobs):
    from org.metadatacenter.util.InvocationContext import current_context
    tasks = shell_tasks(plan)
    edges = dependencies(tasks)
    console = Console()
    records = []
    timing_token = _timings.set(records)
    cancel_token = _cancel.set(threading.Event())
    start = time.monotonic()
    results = {}
    directory = Path(Util.cedar_home) / '.cedar' / 'build-reports'
    directory.mkdir(parents=True, exist_ok=True)
    report = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8] + '.json')
    # Resolve lazy shared state before workers start.
    executors = current_context().task_executors
    console.print(f'Build concurrency: {jobs} repositories; '
                  f'{current_context().settings.maven_threads} Maven threads; '
                  f'{current_context().settings.build_workers} workers per frontend.')
    def run(task):
        return executors[TaskType.SHELL].execute(task, BuildProgress(console, task.repo.name), False)
    try:
        results = run_graph(tasks, edges, run, jobs, fail_fast=GlobalContext.fail_on_error(),
                            on_status=lambda i, state: console.print(f'{state}: {tasks[i].repo.get_fqn()}', markup=False))
        if any(results.values()):
            raise SystemExit(1)
    finally:
        report.write_text(json.dumps({'schemaVersion': 1, 'plan': plan.name, 'jobs': jobs,
            'mavenThreads': current_context().settings.maven_threads,
            'workers': current_context().settings.build_workers,
            'seconds': round(time.monotonic() - start, 3), 'commands': records,
            'tasks': [{'repository': t.repo.get_fqn(), 'exitCode': results.get(i),
                       'dependencies': [tasks[d].repo.get_fqn() for d in sorted(edges[i])]}
                      for i, t in enumerate(tasks)]}, indent=2) + '\n')
        console.print(f'Build timings: {report}', markup=False)
        _timings.reset(timing_token)
        _cancel.reset(cancel_token)

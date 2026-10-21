"""Bounded build scheduling; Maven owns its module graph, npm edges come from manifests.

Only isolated frontend tasks overlap. Other commands remain exclusive barriers in
plan order. Context copies retain this invocation's artifact selection and evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context, ContextVar
import queue
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
from org.metadatacenter.util.BuildSafety import build_temporary_root
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.util.worker_pool import WorkerPool

from org.metadatacenter.util.InvocationContext import invocation_environment, process_cancellation as _cancel
_timings = ContextVar('build_timings', default=None)
_package_ready = ContextVar('build_package_ready', default=None)
_worker_pool = ContextVar('build_worker_pool', default=None)


def worker_pool():
    """The pool of test workers the running build's repositories share, or None outside a build."""
    return _worker_pool.get()


def package_ready():
    """Tell the scheduler that the running task has packaged what it publishes.

    Its consumers may then start while its own checks continue. Outside a scheduled build this
    does nothing.
    """
    notify = _package_ready.get()
    if notify is not None:
        notify()


def record_timing(repo, command, started, code, stages=()):
    records = _timings.get()
    if records is not None:
        records.append({'repository': repo, 'command': command,
                        'seconds': round(time.monotonic() - started, 3), 'exitCode': code,
                        'stages': list(stages)})


def record_dropped_optional_dependencies(repo, command, packages):
    """Name the optional dependencies npm dropped in the latest recorded run of a command.

    The run after it, if any, shows whether installing again recovered them.
    """
    records = _timings.get()
    if records is None:
        return
    for record in reversed(records):
        if record['repository'] == repo and record['command'] == command:
            record['droppedOptionalDependencies'] = list(packages)
            return


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
    """Drain running jobs on failure; never launch a failed producer's consumers.

    A consumer starts once every producer has either finished or called `package_ready`. A
    producer that packages early still occupies its slot until its checks finish, and if they fail
    its waiting consumers are blocked as if it had failed before packaging.
    """
    remaining, running, results, packaged = set(range(len(nodes))), {}, {}, set()
    events = queue.SimpleQueue()

    def satisfied(producer):
        return results.get(producer) == 0 or (producer in packaged and producer not in results)

    def body(i):
        _package_ready.set(lambda: events.put(('packaged', i)))
        return run(nodes[i])

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
                        if not all(satisfied(producer) for producer in edges[i]):
                            continue
                        remaining.remove(i)
                        on_status(i, 'running')
                        future = pool.submit(copy_context().run, body, i)
                        running[future] = i
                        future.add_done_callback(lambda _future, i=i: events.put(('done', i)))
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
                kind, i = events.get()
                if kind == 'packaged':
                    packaged.add(i)
                    on_status(i, 'packaged')
                    continue
                future = next(future for future, index in running.items() if index == i)
                running.pop(future)
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
    workers = current_context().settings.build_workers
    # Each frontend may use up to its own budget, and those running at once share one the size of
    # the host, so --jobs fills idle cores without oversubscribing busy ones.
    pool_size = max(workers, os.cpu_count() or 1)
    frontends = any(task.parameters.get('isolated_frontend_build') for task in tasks)
    console.print(f'Build concurrency: {jobs} repositories; '
                  f'{current_context().settings.maven_threads} Maven threads; '
                  f'{workers} workers per frontend' + (f', {pool_size} shared.' if frontends else '.'))
    def run(task):
        return executors[TaskType.SHELL].execute(task, BuildProgress(console, task.repo.name), False)
    # Seconds from the start of the build at which each task reached each state, so a report shows
    # what waited on what rather than only how long each command took.
    timeline = {}
    def status(i, state):
        timeline.setdefault(i, {})[state] = round(time.monotonic() - start, 3)
        console.print(f'{state}: {tasks[i].repo.get_fqn()}', markup=False)
    pool = (WorkerPool.created(build_temporary_root(dict(invocation_environment())), pool_size)
            if frontends else nullcontext())
    try:
        with pool as shared:
            pool_token = _worker_pool.set(shared)
            try:
                results = run_graph(tasks, edges, run, jobs, fail_fast=GlobalContext.fail_on_error(),
                                    on_status=status)
            finally:
                _worker_pool.reset(pool_token)
        if any(results.values()):
            raise SystemExit(1)
    finally:
        report.write_text(json.dumps({'schemaVersion': 1, 'plan': plan.name, 'jobs': jobs,
            'mavenThreads': current_context().settings.maven_threads,
            'workers': workers, 'workerPool': pool_size if frontends else None,
            'seconds': round(time.monotonic() - start, 3), 'commands': records,
            'tasks': [{'repository': t.repo.get_fqn(), 'exitCode': results.get(i),
                       'dependencies': [tasks[d].repo.get_fqn() for d in sorted(edges[i])],
                       'timeline': timeline.get(i, {})}
                      for i, t in enumerate(tasks)]}, indent=2) + '\n')
        console.print(f'Build timings: {report}', markup=False)
        _timings.reset(timing_token)
        _cancel.reset(cancel_token)

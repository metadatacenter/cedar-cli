"""Explicit invocation state, with scoped adapters for the existing worker APIs."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cached_property
import os
import platform


@dataclass
class InvocationSettings:
    do_fail_on_error: bool = True
    skip_tests: bool = False
    shell_path: str = '/bin/bash'

    def get_sed_replace_in_place(self):
        return "sed -i ''" if platform.system() == 'Darwin' else 'sed -i'


@dataclass
class InvocationContext:
    environment: dict = field(default_factory=lambda: dict(os.environ))
    settings: InvocationSettings = field(default_factory=InvocationSettings)
    task_type: object = None

    @property
    def cedar_home(self):
        return self.environment.get('CEDAR_HOME')

    @cached_property
    def repos(self):
        from org.metadatacenter.config.ReposFactory import ReposFactory
        return ReposFactory.build_repos()

    @cached_property
    def servers(self):
        from org.metadatacenter.config.ServersFactory import ServersFactory
        return ServersFactory.build_servers()

    @cached_property
    def subdomains(self):
        from org.metadatacenter.config.SubdomainsFactory import SubdomainsFactory
        return SubdomainsFactory.build_subdomains()

    @cached_property
    def task_operators(self):
        from org.metadatacenter.model.TaskType import TaskType
        from org.metadatacenter.operator.BuildOperator import BuildOperator
        from org.metadatacenter.operator.PublishOperator import PublishOperator
        return {TaskType.BUILD: BuildOperator(), TaskType.PUBLISH: PublishOperator()}

    @cached_property
    def task_executors(self):
        from org.metadatacenter.model.TaskType import TaskType
        from org.metadatacenter.taskexecutor.BuildTaskExecutor import BuildTaskExecutor
        from org.metadatacenter.taskexecutor.PublishTaskExecutor import PublishTaskExecutor
        from org.metadatacenter.taskexecutor.ShellWrapperTaskExecutor import ShellWrapperTaskExecutor
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
        from org.metadatacenter.taskexecutor.NoopTaskExecutor import NoopTaskExecutor
        return {
            TaskType.BUILD: BuildTaskExecutor(), TaskType.PUBLISH: PublishTaskExecutor(),
            TaskType.SHELL_WRAPPER: ShellWrapperTaskExecutor(),
            TaskType.SHELL: ShellTaskExecutor(), TaskType.NOOP: NoopTaskExecutor(),
        }


_active = ContextVar('cedar_invocation', default=None)
_legacy = ContextVar('cedar_library_context', default=None)


def current_context():
    """Return the bound invocation, or a lazy context for direct library callers."""
    context = _active.get()
    if context is not None:
        return context
    context = _legacy.get()
    if context is None:
        # Direct library calls retain ambient-environment behavior. CLI entry
        # points always bind a fresh context with a copied environment instead.
        context = InvocationContext(environment=os.environ)
        _legacy.set(context)
    return context


def invocation_environment():
    context = _active.get()
    return context.environment if context is not None else os.environ


@contextmanager
def use_context(context):
    token = _active.set(context)
    try:
        yield context
    finally:
        _active.reset(token)


class ContextAttribute:
    """Read an invocation attribute through an existing class-level API."""
    def __init__(self, path):
        self.path = path.split('.')

    def __get__(self, instance, owner):
        value = current_context()
        for name in self.path:
            value = getattr(value, name)
        return value


def process_environment(override=None):
    """Subprocesses inherit the invocation's resolved profile unless explicitly overridden."""
    return invocation_environment() if override is None else override

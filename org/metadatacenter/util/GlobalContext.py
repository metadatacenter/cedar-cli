"""Compatibility accessors; mutable state belongs to an InvocationContext."""
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.InvocationContext import (
    ContextAttribute, current_context, invocation_environment,
)
from org.metadatacenter.util.Util import Util

UTF_8 = 'utf-8'


class GlobalContext:
    repos = ContextAttribute('repos')
    servers = ContextAttribute('servers')
    subdomains = ContextAttribute('subdomains')
    task_type = ContextAttribute('task_type')
    task_operators = ContextAttribute('task_operators')
    task_executors = ContextAttribute('task_executors')

    def __init__(self):
        Util.check_cedar_home()

    @classmethod
    def mark_global_task_type(cls, task_type: TaskType):
        current_context().task_type = task_type

    @classmethod
    def init_task_operators(cls):
        current_context().__dict__.pop('task_operators', None)
        return current_context().task_operators

    @classmethod
    def init_task_executors(cls):
        current_context().__dict__.pop('task_executors', None)
        return current_context().task_executors

    @classmethod
    def get_task_operator(cls, task_type):
        return cls.task_operators.get(task_type)

    @classmethod
    def get_task_executor(cls, task_type):
        return cls.task_executors.get(task_type)

    @classmethod
    def get_ca_common_name(cls):
        return invocation_environment()[Const.CEDAR_CA_COMMON_NAME]

    @classmethod
    def fail_on_error(cls):
        return current_context().settings.do_fail_on_error

    @classmethod
    def mark_do_not_fail(cls):
        current_context().settings.do_fail_on_error = False

    @classmethod
    def should_skip_tests(cls):
        return current_context().settings.skip_tests

    @classmethod
    def mark_skip_tests(cls, skip_tests: bool):
        current_context().settings.skip_tests = skip_tests

    @classmethod
    def get_shell(cls):
        return current_context().settings.shell_path

    @classmethod
    def get_sed_replace_in_place(cls):
        return current_context().settings.get_sed_replace_in_place()

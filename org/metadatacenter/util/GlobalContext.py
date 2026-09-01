import os

from rich.console import Console

from CedarCliSettings import CedarCliSettings
from org.metadatacenter.config.ReposFactory import ReposFactory
from org.metadatacenter.config.ServersFactory import ServersFactory
from org.metadatacenter.config.SubdomainsFactory import SubdomainsFactory
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.Util import Util

console = Console()

UTF_8 = 'utf-8'


class GlobalContext(object):
    repos = ReposFactory.build_repos()
    servers = ServersFactory.build_servers()
    subdomains = SubdomainsFactory.build_subdomains()
    task_type = None
    task_operators = {}
    task_executors = {}

    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(GlobalContext, cls).__new__(cls)
            cls.instance.init_task_operators()
            cls.instance.init_task_executors()
        return cls.instance

    def __init__(self):
        Util.check_cedar_home()

    @classmethod
    def mark_global_task_type(cls, task_type: TaskType):
        cls.task_type = task_type

    @classmethod
    def init_task_operators(cls):
        from org.metadatacenter.operator.BuildOperator import BuildOperator
        from org.metadatacenter.operator.PublishOperator import PublishOperator
        cls.task_operators = {
            TaskType.BUILD: BuildOperator(),
            TaskType.PUBLISH: PublishOperator(),
        }

    @classmethod
    def init_task_executors(cls):
        from org.metadatacenter.taskexecutor.BuildTaskExecutor import BuildTaskExecutor
        from org.metadatacenter.taskexecutor.PublishTaskExecutor import PublishTaskExecutor
        from org.metadatacenter.taskexecutor.ShellWrapperTaskExecutor import ShellWrapperTaskExecutor
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
        from org.metadatacenter.taskexecutor.NoopTaskExecutor import NoopTaskExecutor
        cls.task_executors = {
            TaskType.BUILD: BuildTaskExecutor(),
            TaskType.PUBLISH: PublishTaskExecutor(),
            TaskType.SHELL_WRAPPER: ShellWrapperTaskExecutor(),
            TaskType.SHELL: ShellTaskExecutor(),
            TaskType.NOOP: NoopTaskExecutor()
        }

    @classmethod
    def get_task_operator(cls, task_type):
        if task_type in cls.task_operators:
            return cls.task_operators[task_type]
        else:
            return None

    @classmethod
    def get_task_executor(cls, task_type):
        if task_type in cls.task_executors:
            return cls.task_executors[task_type]
        else:
            return None

    @classmethod
    def get_ca_common_name(cls):
        return os.environ[Const.CEDAR_CA_COMMON_NAME]

    @classmethod
    def fail_on_error(cls):
        return CedarCliSettings.do_fail_on_error

    @classmethod
    def mark_do_not_fail(cls):
        CedarCliSettings.do_fail_on_error = False

    @classmethod
    def should_skip_tests(cls):
        return CedarCliSettings.skip_tests

    @classmethod
    def mark_skip_tests(cls, skip_tests: bool):
        CedarCliSettings.skip_tests = skip_tests

    @classmethod
    def get_shell(cls):
        return CedarCliSettings.shell_path

    @classmethod
    def get_sed_replace_in_place(cls):
        return CedarCliSettings.get_sed_replace_in_place()

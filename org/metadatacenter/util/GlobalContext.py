from rich.console import Console

from org.metadatacenter.model.ReposFactory import ReposFactory
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.util.Util import Util

console = Console()


class GlobalContext(object):
    repos = ReposFactory.build_repos()
    operator = Operator()
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
        from org.metadatacenter.operator.DeployOperator import DeployOperator
        from org.metadatacenter.operator.ReleasePrepareOperator import ReleasePrepareOperator
        cls.task_operators = {
            TaskType.BUILD: BuildOperator(),
            TaskType.DEPLOY: DeployOperator(),
            TaskType.RELEASE_PREPARE: ReleasePrepareOperator()
        }

    @classmethod
    def init_task_executors(cls):
        from org.metadatacenter.taskexecutor.BuildTaskExecutor import BuildTaskExecutor
        from org.metadatacenter.taskexecutor.DeployTaskExecutor import DeployTaskExecutor
        from org.metadatacenter.taskexecutor.ReleasePrepareTaskExecutor import ReleasePrepareTaskExecutor
        from org.metadatacenter.taskexecutor.ShellWrapperTaskExecutor import ShellWrapperTaskExecutor
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
        from org.metadatacenter.taskexecutor.NoopTaskExecutor import NoopTaskExecutor
        cls.task_executors = {
            TaskType.BUILD: BuildTaskExecutor(),
            TaskType.DEPLOY: DeployTaskExecutor(),
            TaskType.RELEASE_PREPARE: ReleasePrepareTaskExecutor(),
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

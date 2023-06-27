import os

from rich.console import Console

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
        from org.metadatacenter.operator.DeployOperator import DeployOperator
        from org.metadatacenter.operator.ReleasePrepareOperator import ReleasePrepareOperator
        from org.metadatacenter.operator.ReleaseRollbackOperator import ReleaseRollbackOperator
        from org.metadatacenter.operator.ReleaseCleanupOperator import ReleaseCleanupOperator
        from org.metadatacenter.operator.ReleaseCommitOperator import ReleaseCommitOperator
        from org.metadatacenter.operator.ReleaseBranchCheckoutOperator import ReleaseBranchCheckoutOperator
        cls.task_operators = {
            TaskType.BUILD: BuildOperator(),
            TaskType.DEPLOY: DeployOperator(),
            TaskType.RELEASE_PREPARE: ReleasePrepareOperator(),
            TaskType.RELEASE_ROLLBACK: ReleaseRollbackOperator(),
            TaskType.RELEASE_COMMIT: ReleaseCommitOperator(),
            TaskType.RELEASE_CLEANUP: ReleaseCleanupOperator(),
            TaskType.RELEASE_BRANCH_CHECKOUT: ReleaseBranchCheckoutOperator()
        }

    @classmethod
    def init_task_executors(cls):
        from org.metadatacenter.taskexecutor.BuildTaskExecutor import BuildTaskExecutor
        from org.metadatacenter.taskexecutor.DeployTaskExecutor import DeployTaskExecutor
        from org.metadatacenter.taskexecutor.ReleasePrepareTaskExecutor import ReleasePrepareTaskExecutor
        from org.metadatacenter.taskexecutor.ShellWrapperTaskExecutor import ShellWrapperTaskExecutor
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
        from org.metadatacenter.taskexecutor.NoopTaskExecutor import NoopTaskExecutor
        from org.metadatacenter.taskexecutor.ReleaseRollbackTaskExecutor import ReleaseRollbackTaskExecutor
        from org.metadatacenter.taskexecutor.ReleaseCleanupTaskExecutor import ReleaseCleanupTaskExecutor
        from org.metadatacenter.taskexecutor.ReleaseCommitTaskExecutor import ReleaseCommitTaskExecutor
        from org.metadatacenter.taskexecutor.ReleaseBranchCheckoutTaskExecutor import ReleaseBranchCheckoutTaskExecutor
        cls.task_executors = {
            TaskType.BUILD: BuildTaskExecutor(),
            TaskType.DEPLOY: DeployTaskExecutor(),
            TaskType.RELEASE_PREPARE: ReleasePrepareTaskExecutor(),
            TaskType.RELEASE_ROLLBACK: ReleaseRollbackTaskExecutor(),
            TaskType.RELEASE_COMMIT: ReleaseCommitTaskExecutor(),
            TaskType.RELEASE_CLEANUP: ReleaseCleanupTaskExecutor(),
            TaskType.RELEASE_BRANCH_CHECKOUT: ReleaseBranchCheckoutTaskExecutor(),
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

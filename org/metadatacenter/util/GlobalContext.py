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

    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(GlobalContext, cls).__new__(cls)
            cls.instance.init_task_operators()
        return cls.instance

    def __init__(self):
        from org.metadatacenter.util.TaskListExecutor import TaskListExecutor
        self.task_list_executor = TaskListExecutor()
        Util.check_cedar_home()

    # def trigger_post_task(self, repo: Repo, parent_task: Task):
    #     self.task_list_executor.post_task(repo, parent_task)

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
    def get_task_operator(cls, task_type):
        if task_type in cls.task_operators:
            return cls.task_operators[task_type]
        else:
            return None

from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.ReleaseRollbackShellTaskFactory import ReleaseRollbackShellTaskFactory

console = Console()


class ReleaseRollbackOperator(Operator):

    def __init__(self):
        super().__init__()

    @staticmethod
    def expand(task: PlanTask):
        repo_list = [task.repo]

        for repo in repo_list:
            shell_wrapper = PlanTask("Rolling back repo", TaskType.SHELL_WRAPPER, repo)
            shell_wrapper.add_task_as_task(ReleaseRollbackShellTaskFactory.rollback_generic(repo))
            task.add_task_as_task(shell_wrapper)

from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.PreReleaseBranchType import PreReleaseBranchType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.ReleasePrepareCreateBranchShellTaskFactory import ReleasePrepareCreateBranchShellTaskFactory

console = Console()


class ReleasePrepareCreateBranchOperator(Operator):

    def __init__(self):
        super().__init__()

    @staticmethod
    def expand(task: PlanTask):
        ReleasePrepareCreateBranchOperator.expand_for_release(task, task.parameters['branch_type'])

    @classmethod
    def expand_for_release(cls, task, branch_type: PreReleaseBranchType):
        repo_list = [task.repo]

        for repo in repo_list:
            shell_wrapper = PlanTask("Create branch for project", TaskType.SHELL_WRAPPER, repo)
            shell_wrapper.add_task_as_task(ReleasePrepareCreateBranchShellTaskFactory.create_branch(repo, branch_type))
            task.add_task_as_task(shell_wrapper)

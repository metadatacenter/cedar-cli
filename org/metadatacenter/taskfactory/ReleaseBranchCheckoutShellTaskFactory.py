from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType


class ReleaseBranchCheckoutShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def checkout_generic(cls, repo: Repo, parent_task: PlanTask) -> PlanTask:
        task = PlanTask("Check out branch on repo", TaskType.SHELL, repo)
        task.parameters = parent_task.parameters
        branch = task.parameters['branch']
        task.command_list = [
            'git checkout ' + branch
        ]
        return task

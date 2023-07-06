from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.PrePostType import PrePostType
from org.metadatacenter.model.PreReleaseBranchType import PreReleaseBranchType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.BuildShellTaskFactory import BuildShellTaskFactory
from org.metadatacenter.taskfactory.ReleaseCleanupShellTaskFactory import ReleaseCleanupShellTaskFactory

console = Console()


class ReleaseCleanupOperator(Operator):

    def __init__(self):
        super().__init__()

    @staticmethod
    def expand(task: PlanTask):
        ReleaseCleanupOperator.expand_for_release(task)

    @classmethod
    def expand_for_release(cls, task):
        repo_list = [task.repo]

        for repo in repo_list:
            if repo.pre_post_type == PrePostType.SUB:
                not_handled = PlanTask("Skip sub repo", TaskType.NOOP, repo)
                not_handled.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(not_handled)
            elif repo.pre_post_type == PrePostType.NONE or repo.pre_post_type == PrePostType.PRE:
                shell_wrapper = PlanTask("Clean up repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleaseCleanupShellTaskFactory.cleanup_generic(repo))
                task.add_task_as_task(shell_wrapper)

from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.PrePostType import PrePostType
from org.metadatacenter.model.RepoRelationType import RepoRelationType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.BuildOperator import BuildOperator
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.BuildShellTaskFactory import BuildShellTaskFactory
from org.metadatacenter.taskfactory.ReleasePrepareShellTaskFactory import ReleasePrepareShellTaskFactory
from org.metadatacenter.taskfactory.ReleaseRollbackShellTaskFactory import ReleaseRollbackShellTaskFactory
from org.metadatacenter.util.GlobalContext import GlobalContext

console = Console()


class ReleaseRollbackOperator(Operator):

    def __init__(self):
        super().__init__()

    @staticmethod
    def expand(task: PlanTask):
        repo_list = [task.repo]
        # repo_list_flat = Util.get_flat_repo_list(repo_list)
        for repo in repo_list:
            shell_wrapper = PlanTask("Prepare rollback of repo", TaskType.SHELL_WRAPPER, repo)
            shell_wrapper.add_task_as_task(ReleaseRollbackShellTaskFactory.rollback_generic(repo))
            task.add_task_as_task(shell_wrapper)

            #
            # source_of_relation = GlobalContext.repos.get_relation(repo, RepoRelationType.IS_SOURCE_OF)
            # if source_of_relation is not None:
            #     BuildOperator.handle_is_source_of(source_of_relation, task)

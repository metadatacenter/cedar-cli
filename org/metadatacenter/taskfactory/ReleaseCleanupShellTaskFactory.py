from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.taskfactory.ReleaseRollbackShellTaskFactory import ReleaseRollbackShellTaskFactory
from org.metadatacenter.util.Util import Util


class ReleaseCleanupShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def cleanup_generic(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Cleanup of generic repo", TaskType.SHELL, repo)

        pre_branch, post_branch = Util.get_cleanup_vars()

        is_delete_pre_branch = False
        is_delete_post_branch = False

        if pre_branch.startswith('release/pre'):
            is_delete_pre_branch = True
        if post_branch.startswith('release/post'):
            is_delete_post_branch = True

        if not is_delete_pre_branch and not is_delete_post_branch:
            task = PlanTask("Cleanup not supported", TaskType.SHELL, repo)
            task.command_list = []
        else:
            task.command_list = [
                *ReleaseRollbackShellTaskFactory.macro_checkout_develop(),
                *(ReleaseRollbackShellTaskFactory.macro_delete_local_and_remote_branch(pre_branch) if is_delete_pre_branch else []),
                *(ReleaseRollbackShellTaskFactory.macro_delete_local_and_remote_branch(post_branch) if is_delete_post_branch else [])
            ]
        return task

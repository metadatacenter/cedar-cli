from rich.console import Console
from rich.progress import Progress

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.PreReleaseBranchType import PreReleaseBranchType
from org.metadatacenter.taskexecutor.TaskExecutor import TaskExecutor
from org.metadatacenter.util.Util import Util

console = Console()


class ReleasePrepareTaskExecutor(TaskExecutor):

    def __init__(self):
        super().__init__()

    def execute(self, task: PlanTask, job_progress: Progress):
        super().display_header(task, job_progress, 'cyan', "Release prepare task executor")

        _, pre_branch, tag_name = Util.get_release_vars(PreReleaseBranchType.RELEASE)
        _, post_branch, _ = Util.get_release_vars(PreReleaseBranchType.NEXT_DEV)

        Util.write_cedar_file(Util.LAST_RELEASE_PRE_BRANCH, pre_branch)
        Util.write_cedar_file(Util.LAST_RELEASE_POST_BRANCH, post_branch)
        Util.write_cedar_file(Util.LAST_RELEASE_TAG, tag_name)


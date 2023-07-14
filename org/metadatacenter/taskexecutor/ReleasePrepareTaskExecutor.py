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

    def execute(self, task: PlanTask, job_progress: Progress, dry_run: bool):
        super().display_header(task, job_progress, 'cyan', "Release prepare task executor #" + str(task.node_id))

        release_version, pre_branch, tag_name = Util.get_release_vars(PreReleaseBranchType.RELEASE)
        next_dev_version, post_branch, _ = Util.get_release_vars(PreReleaseBranchType.NEXT_DEV)

        Util.write_cedar_file(Util.LAST_RELEASE_PRE_BRANCH, pre_branch)
        Util.write_cedar_file(Util.LAST_RELEASE_POST_BRANCH, post_branch)
        Util.write_cedar_file(Util.LAST_RELEASE_TAG, tag_name)
        Util.write_cedar_file(Util.LAST_RELEASE_VERSION, release_version)
        Util.write_cedar_file(Util.LAST_RELEASE_NEXT_DEV_VERSION, next_dev_version)



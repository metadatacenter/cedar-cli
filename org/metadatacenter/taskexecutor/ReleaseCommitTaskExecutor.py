from rich.console import Console
from rich.progress import Progress

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.taskexecutor.TaskExecutor import TaskExecutor

console = Console()


class ReleaseCommitTaskExecutor(TaskExecutor):

    def __init__(self):
        super().__init__()

    def execute(self, task: PlanTask, job_progress: Progress):
        super().display_header(task, job_progress, 'cyan', "Commit prepared release task executor")

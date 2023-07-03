from rich.console import Console
from rich.progress import Progress

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.taskexecutor.TaskExecutor import TaskExecutor

console = Console()


class NoopTaskExecutor(TaskExecutor):

    def __init__(self):
        super().__init__()

    def execute(self, task: PlanTask, job_progress: Progress, dry_run: bool):
        super().display_header(task, job_progress, 'bright_black', "Noop task executor")

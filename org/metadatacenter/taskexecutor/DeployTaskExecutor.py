from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.style import Style

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.taskexecutor.TaskExecutor import TaskExecutor

console = Console()


class DeployTaskExecutor(TaskExecutor):

    def __init__(self):
        super().__init__()

    def execute(self, task: PlanTask, job_progress: Progress):
        super().display_header(task, job_progress, 'cyan', "Deploy task executor")

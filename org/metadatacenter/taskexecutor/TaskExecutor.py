import abc
from abc import ABC

from rich.panel import Panel
from rich.progress import Progress
from rich.style import Style

from org.metadatacenter.model.PlanTask import PlanTask


class TaskExecutor(ABC):

    def __init__(self):
        super().__init__()

    @abc.abstractmethod
    def execute(self, plan: PlanTask, job_progress: Progress):
        pass

    def display_header(self, task: PlanTask, job_progress: Progress, color: str, title: str):
        msg = task.name + (" => " + task.repo.pre_post_type if task.repo.pre_post_type is not None else "")
        msg += "\n" + "️ ➡️  " + task.repo.get_wd()
        job_progress.print(Panel(msg, style=Style(color=color), title=title))

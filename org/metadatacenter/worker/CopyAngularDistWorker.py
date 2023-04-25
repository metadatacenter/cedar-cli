from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.Task import Task
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class CopyAngularDistWorker(Worker):
    def __init__(self):
        super().__init__()

    def work(self, task: Task, parent_task_repo=None):
        title = task.title
        progress_text = task.progress_text
        source_path = Util.get_wd(parent_task_repo)
        target_repo = task.parameters['target_repo']
        target_path = Util.get_wd(target_repo)
        msg = "[cyan]" + title
        msg += "\n  Source repo: " + "️ ➡️  " + parent_task_repo.get_wd()
        msg += "\n  Target repo: " + "️ ➡️  " + target_repo.get_wd()
        console.print(Panel(msg, style=Style(color="cyan"), title="Copy Angular Dist worker"))

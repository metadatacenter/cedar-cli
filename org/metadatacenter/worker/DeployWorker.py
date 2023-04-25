from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.Task import Task
from org.metadatacenter.worker.Worker import Worker

console = Console()


class DeployWorker(Worker):
    def __init__(self):
        super().__init__()

    def work(self, task: Task, parent_task_repo=None):
        repo_list = task.repo_list
        title = task.title
        progress_text = task.progress_text
        msg = "[cyan]" + title
        repo_list_flat = self.get_flat_repo_list(repo_list)
        for repo in repo_list_flat:
            msg += "\n  " + "️ ➡️  " + repo.get_wd()
        console.print(Panel(msg, style=Style(color="cyan"), title="Deploy worker"))

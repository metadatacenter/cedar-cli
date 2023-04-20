from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.Task import Task
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.Worker import Worker

console = Console()


class BuildWorker(Worker):
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
        console.print(Panel(msg, style=Style(color="cyan"), title="Build worker"))
        for repo in repo_list_flat:
            handled = False
            if repo.repo_type == RepoType.JAVA_WRAPPER:
                self.execute_shell(repo, ["mvn clean install -DskipTests"], progress_text)
                handled = True
            elif repo.repo_type == RepoType.ANGULAR:
                self.execute_shell(repo, ["npm install --legacy-peer-deps; ng build --configuration=production"],
                                   progress_text)
                handled = True
            elif repo.repo_type == RepoType.ANGULAR_JS:
                self.execute_shell(repo, ["npm install"], progress_text)
                handled = True
            if not handled:
                self.execute_none(repo, progress_text)
            GlobalContext().trigger_post_task(repo, task)

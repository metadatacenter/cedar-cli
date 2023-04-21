import jsonpickle as jsonpickle
from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model import TaskList
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.Task import Task
from org.metadatacenter.model.WorkerType import WorkerType
from org.metadatacenter.worker.BuildWorker import BuildWorker
from org.metadatacenter.worker.CopyAngularDistWorker import CopyAngularDistWorker
from org.metadatacenter.worker.DeployWorker import DeployWorker

console = Console()


class TaskListExecutor:

    def __init__(self):
        self.worker_map = {
            WorkerType.BUILD: BuildWorker(),
            WorkerType.DEPLOY: DeployWorker(),
            WorkerType.COPY_ANGULAR_DIST: CopyAngularDistWorker(),
        }

    def execute_task_list(self, task_list: TaskList):
        msg = "[bright_cyan]"
        sep = ""
        for task in task_list.tasks:
            msg += sep + " ⚙️️  " + task.title + " " + task.worker_type
            sep = "\n"
        console.print(Panel(msg, style=Style(color="bright_cyan"), title="Execute task list", title_align = "left"))
        for task in task_list.tasks:
            worker = self.get_worker(task.worker_type)
            worker.work(task)

    def get_worker(self, worker_type: WorkerType):
        if worker_type not in self.worker_map:
            self.worker_map[worker_type] = self.build_worker_for(worker_type)
        return self.worker_map[worker_type]

    def post_task(self, repo: Repo, parent_task: Task):
        for task_type, post_tasks in repo.post_tasks.items():
            if task_type == parent_task.worker_type:
                for post_task in post_tasks:
                    msg = "  Repo       : " + "️ 🏁  " + repo.get_wd()
                    msg += "\n  Task type  : " + "️ 🏁  " + post_task.worker_type
                    msg += "\n  Parameters : " + "️ 🏁  " + jsonpickle.encode(post_task.parameters)
                    console.print(Panel(msg, style=Style(color="bright_cyan"), title="Post task"))
                    worker = self.get_worker(post_task.worker_type)
                    worker.work(post_task, repo)

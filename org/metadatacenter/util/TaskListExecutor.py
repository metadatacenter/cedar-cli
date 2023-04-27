from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model import TaskList
from org.metadatacenter.model.WorkerType import WorkerType
from org.metadatacenter.worker.ReleasePrepareWorker import ReleasePrepareWorker

console = Console()


class TaskListExecutor:

    def __init__(self):
        self.worker_map = {
            WorkerType.RELEASE_PREPARE: ReleasePrepareWorker(),
        }

    def execute_task_list(self, task_list: TaskList):
        msg = "[bright_cyan]"
        sep = ""
        for task in task_list.tasks:
            msg += sep + " ⚙️️  " + task.title + " " + task.worker_type
            sep = "\n"
        console.print(Panel(msg, style=Style(color="bright_cyan"), title="Execute task list", title_align="left"))
        for task in task_list.tasks:
            worker = self.get_worker(task.worker_type)
            worker.work(task)

    def get_worker(self, worker_type: WorkerType):
        return self.worker_map[worker_type]

    # def post_task(self, repo: Repo, parent_task: Task):
    #     for task_type, post_tasks in repo.post_tasks.items():
    #         if task_type == parent_task.worker_type:
    #             for post_task in post_tasks:
    #                 msg = "  Repo       : " + "️ 🏁  " + repo.get_wd()
    #                 msg += "\n  Task type  : " + "️ 🏁  " + post_task.worker_type
    #                 msg += "\n  Parameters : " + "️ 🏁  " + jsonpickle.encode(post_task.parameters)
    #                 console.print(Panel(msg, style=Style(color="bright_cyan"), title="Post task"))
    #                 worker = self.get_worker(post_task.worker_type)
    #                 worker.work(post_task, repo)

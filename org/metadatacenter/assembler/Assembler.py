from rich.console import Console

from org.metadatacenter.model.Task import Task
from org.metadatacenter.model.TaskList import TaskList
from org.metadatacenter.util.GlobalContext import GlobalContext

console = Console()


class Assembler:
    def __init__(self):
        self.task_list = TaskList()
        self.task_list_executor = GlobalContext().task_list_executor

    def create_task_list(self):
        self.task_list = TaskList()

    def execute_task_list(self):
        self.task_list_executor.execute_task_list(self.task_list)

    def add_task(self, task: Task):
        self.task_list.add_task(task)

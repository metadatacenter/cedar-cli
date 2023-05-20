from rich.console import Console

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType

console = Console()


class Plan:
    def __init__(self, name: str):
        self.name = name
        self.tasks = []

    def add_task(self, name: str, task_type: TaskType, repo_list: list[Repo], parameters: dict = {}):
        from org.metadatacenter.model.PlanTask import PlanTask
        proper_list = list(filter(None, repo_list))
        to_expand = []
        if len(proper_list) == 1:
            task = PlanTask(name, task_type, proper_list[0], parameters)
            self.tasks.append(task)
            to_expand.append(task)
        else:
            for repo in proper_list:
                task = PlanTask(name, task_type, repo, parameters)
                self.tasks.append(task)
                to_expand.append(task)
        self.expand_tasks(to_expand)

    def add_task_as_task(self, task):
        self.tasks.append(task)
        self.expand_tasks([task])

    def add_task_as_task_no_expand(self, task):
        self.tasks.append(task)

    def expand_tasks(self, tasks):
        from org.metadatacenter.util.GlobalContext import GlobalContext
        for task in tasks:
            GlobalContext.operator.expand_task(task)

    def get_max_depth(self):
        return self.get_max_depth_recursively(self, 0)

    def get_max_depth_recursively(self, plan: 'Plan', depth):
        max_depth = 0
        for task in plan.tasks:
            max_depth = max(max_depth, self.get_max_depth_recursively(task, depth))
        return max_depth + 1

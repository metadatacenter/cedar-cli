from rich.console import Console

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.Operator import Operator

console = Console()


class Plan:
    def __init__(self, name: str):
        self.name = name
        self.tasks = []

    def add_task(self, name: str, task_type: TaskType, repo_list: list[Repo], parameters=None):
        if parameters is None:
            parameters = dict()
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
        Plan.expand_tasks(to_expand)

    def add_task_as_task(self, task):
        self.tasks.append(task)
        Plan.expand_tasks([task])

    def add_task_as_task_no_expand(self, task):
        self.tasks.append(task)

    @staticmethod
    def expand_tasks(tasks):
        for task in tasks:
            Operator.expand_task(task)

    def get_max_depth(self):
        return self.get_max_depth_recursively(self, 0)

    def get_max_depth_recursively(self, plan: 'Plan', depth):
        max_depth = 0
        for task in plan.tasks:
            max_depth = max(max_depth, self.get_max_depth_recursively(task, depth))
        return max_depth + 1

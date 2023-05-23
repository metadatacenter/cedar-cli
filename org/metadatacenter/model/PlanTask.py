from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType


class PlanTask(Plan):

    def __init__(self, name: str, task_type: TaskType, repo: Repo, parameters=None):
        super().__init__(name)
        if parameters is None:
            parameters = dict()
        self.task_type = task_type
        self.repo = repo
        self.command_list = None
        self.variables = None
        self.parameters = parameters

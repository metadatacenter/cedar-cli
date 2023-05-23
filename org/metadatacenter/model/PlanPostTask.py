from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType


class PlanPostTask(Plan):

    def __init__(self, name: str, task_type: TaskType, parent_task_type: TaskType, variables=None):
        super().__init__(name)
        if variables is None:
            variables = dict()
        self.task_type = task_type
        self.parent_task_type = parent_task_type
        self.variables = variables

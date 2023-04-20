from org.metadatacenter.model.Task import Task


class TaskList:

    def __init__(self, tasks=[]):
        self.tasks = tasks

    def add_task(self, task: Task):
        self.tasks.append(task)

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.ReposFactory import ReposFactory
from org.metadatacenter.model.Task import Task


class GlobalContext(object):
    repos = ReposFactory.build_repos()

    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(GlobalContext, cls).__new__(cls)
        return cls.instance

    def __init__(self):
        from org.metadatacenter.util.TaskListExecutor import TaskListExecutor
        self.task_list_executor = TaskListExecutor()

    def trigger_post_task(self, repo: Repo, parent_task: Task):
        self.task_list_executor.post_task(repo, parent_task)

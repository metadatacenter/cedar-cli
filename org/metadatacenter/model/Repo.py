from org.metadatacenter.model.Task import Task
from org.metadatacenter.model.WorkerType import WorkerType


class Repo:

    def __init__(self, name, repo_type, is_client=False, is_library=False, is_microservice=False, is_private=False, for_docker=False,
                 is_frontend=False, expected_build_lines=100):
        self.name = name
        self.repo_type = repo_type
        self.is_client = is_client
        self.is_library = is_library
        self.is_microservice = is_microservice
        self.is_private = is_private
        self.for_docker = for_docker
        self.is_frontend = is_frontend
        self.expected_build_lines = expected_build_lines
        self.is_sub_repo = False
        self.sub_repos = []
        self.parent_repo = None
        self.post_tasks = {}

    def add_sub_repo(self, sub_repo):
        self.sub_repos.append(sub_repo)
        sub_repo.is_sub_repo = True
        sub_repo.parent_repo = self

    def get_wd(self):
        if self.is_sub_repo:
            return self.parent_repo.name + "/" + self.name
        else:
            return self.name

    def add_post_task(self, task_types: [WorkerType], post_task: Task):
        for task_type in task_types:
            if task_type not in self.post_tasks:
                self.post_tasks[task_type] = []
            self.post_tasks[task_type].append(post_task)

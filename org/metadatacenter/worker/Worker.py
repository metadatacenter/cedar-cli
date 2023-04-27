import os

from rich.console import Console

from org.metadatacenter.model.WorkerType import WorkerType

console = Console()


class Worker:
    worker_type: WorkerType

    @staticmethod
    def get_flat_repo_list(repo_list):
        repos = []
        for repo in repo_list:
            repos.append(repo)
            if len(repo.sub_repos) > 0:
                for sub_repo in repo.sub_repos:
                    repos.append(sub_repo)
        return repos

    def write_cedar_file(self, file_name, content):
        with open(self.get_cedar_file(file_name), "w") as file:
            file.write(content)

    def read_cedar_file(self, file_name):
        path = self.get_cedar_file(file_name)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as file:
            return file.read().rstrip()

    def delete_cedar_file(self, file_name):
        path = self.get_cedar_file(file_name)
        if os.path.exists(path):
            os.remove(path)

    def get_cedar_file(self, file_name):
        parent_path = os.path.expanduser('~/.cedar/')
        if not os.path.exists(parent_path):
            os.makedirs(parent_path)
        return os.path.join(parent_path, file_name)

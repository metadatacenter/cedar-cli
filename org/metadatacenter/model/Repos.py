from rich.console import Console

from org.metadatacenter.model.RepoType import RepoType

console = Console()


class Repos:
    def __init__(self):
        self.map = {}

    def add_repo(self, repo):
        name = repo.name
        if name in self.map:
            console.log("Repo already present in registry:" + name)
        else:
            self.map[name] = repo

    def get_list_top(self):
        return list(self.map.values())

    def get_list_all(self):
        repos = []
        for name, repo in self.map.items():
            repos.append(repo)
            if len(repo.sub_repos) > 0:
                for sub_repo in repo.sub_repos:
                    repos.append(sub_repo)
        return repos

    def get_for_docker_list(self):
        return [repo for repo in list(self.map.values()) if repo.for_docker is True]

    def get_parent(self):
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "parent" in repo.name:
                return repo

    def get_libraries(self):
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "libraries" in repo.name:
                return repo

    def get_project(self):
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "project" in repo.name:
                return repo

    def get_clients(self):
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "clients" in repo.name:
                return repo

    def get_frontends(self):
        repos = []
        for name, repo in self.map.items():
            if repo.is_frontend:
                repos.append(repo)
        return repos

from rich.console import Console

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.Util import Util

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

    def get_list_top(self) -> [Repo]:
        return list(self.map.values())

    def get_list_all(self) -> [Repo]:
        repos = []
        for name, repo in self.map.items():
            repos.append(repo)
            if len(repo.sub_repos) > 0:
                for sub_repo in repo.sub_repos:
                    repos.append(sub_repo)
        return repos

    def get_for_docker_list(self) -> [Repo]:
        return [repo for repo in list(self.map.values()) if repo.for_docker is True]

    def get_parent(self) -> [Repo]:
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "parent" in repo.name:
                return [repo]
        return []

    def get_libraries(self) -> [Repo]:
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "libraries" in repo.name:
                return [repo]
        return []

    def get_project(self) -> [Repo]:
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "project" in repo.name:
                return [repo]
        return []

    def get_clients(self) -> [Repo]:
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "clients" in repo.name:
                return [repo]
        return []

    def get_frontends(self) -> [Repo]:
        repos = []
        for name, repo in self.map.items():
            if repo.is_frontend:
                repos.append(repo)
        return repos

    def get_release_all(self) -> [Repo]:
        repos = []
        repos = repos + Util.get_flat_repo_list(self.get_parent())
        # repos = repos + Util.get_flat_repo_list(self.get_libraries())
        # repos = repos + Util.get_flat_repo_list(self.get_project())
        # repos = repos + Util.get_flat_repo_list(self.get_clients())
        # repos = repos + Util.get_flat_repo_list(self.get_frontends())
        # remainder = list(set(self.get_list_all()) - set(repos))
        # repos = repos + remainder
        return repos

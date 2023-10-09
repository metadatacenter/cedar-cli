import os
from typing import List

from rich.console import Console

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoRelation import RepoRelation
from org.metadatacenter.model.RepoRelationType import RepoRelationType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.Util import Util

console = Console()


class Repos:
    def __init__(self):
        self.map = {}
        self.relations: List[RepoRelation] = []
        self.use_private_repos = Const.CEDAR_DEV_USE_PRIVATE_REPOS in os.environ and os.environ[Const.CEDAR_DEV_USE_PRIVATE_REPOS] == 'true'

    def add_repo(self, repo):
        if repo.is_private and not self.use_private_repos:
            return
        name = repo.name
        if name in self.map:
            console.log("Repo already present in registry:" + name)
        else:
            self.map[name] = repo

    def add_relation(self, relation: RepoRelation):
        self.relations.append(relation)

    def get_relations(self, source_repo: Repo, relation_type: RepoRelationType) -> list[RepoRelation]:
        rels = []
        for rel in self.relations:
            if rel.source_repo.get_fqn() == source_repo.get_fqn() and rel.relation_type == relation_type:
                rels.append(rel)
        return rels

    def get_list_top(self) -> [Repo]:
        return list(self.map.values())

    def get_list_top_for_release(self) -> [Repo]:
        repos = list(self.map.values())
        repos = [repo for repo in repos if not repo.skip_from_release]
        return repos

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
        repos = repos + Util.get_flat_repo_list_pre_post(self.get_parent())
        repos = repos + Util.get_flat_repo_list_pre_post(self.get_libraries())
        repos = repos + Util.get_flat_repo_list_pre_post(self.get_project())
        repos = repos + Util.get_flat_repo_list_pre_post(self.get_clients())
        repos = repos + Util.get_flat_repo_list_pre_post(self.get_frontends())
        remainder = list(set(self.get_list_all()) - set(repos))
        repos = repos + remainder
        repos = [repo for repo in repos if not repo.skip_from_release]
        return repos

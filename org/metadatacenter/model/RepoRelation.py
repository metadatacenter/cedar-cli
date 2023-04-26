from rich.console import Console

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoRelationType import RepoRelationType

console = Console()


class RepoRelation:
    def __init__(self, source_repo: Repo, relation_type: RepoRelationType, target_repo: Repo):
        self.source_repo = source_repo
        self.relation_type = relation_type
        self.target_repo = target_repo

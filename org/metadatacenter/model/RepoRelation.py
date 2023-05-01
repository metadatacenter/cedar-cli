from rich.console import Console

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoRelationType import RepoRelationType

console = Console()


class RepoRelation:
    TARGET_SUB_FOLDER = "target_sub_folder"
    SOURCE_SUB_FOLDER = "source_sub_folder"
    SOURCE_SELECTOR = "source-selector"
    DESTINATION_CONCAT = "destination_concat"

    def __init__(self, source_repo: Repo, relation_type: RepoRelationType, target_repo: Repo, parameters: dict = {}):
        self.source_repo = source_repo
        self.relation_type = relation_type
        self.target_repo = target_repo
        self.parameters = parameters

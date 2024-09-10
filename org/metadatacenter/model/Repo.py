from typing import List

from org.metadatacenter.model.ArtifactType import ArtifactType
from org.metadatacenter.model.PrePostType import PrePostType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.VersionType import VersionType


class Repo:

    def __init__(self, name: str, repo_type: RepoType, artifact_type: ArtifactType,
                 version_list: List[VersionType],
                 is_client=False, is_library=False, is_microservice=False, is_private=False, for_docker=False,
                 is_frontend=False, expected_build_lines=100,
                 allow_different_version=False, skip_from_release=False, skip_npm_install=False):
        self.name = name
        self.repo_type = repo_type
        self.artifact_type = artifact_type
        self.version_list = version_list
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
        self.pre_post_type: PrePostType = PrePostType.NONE
        self.allow_different_version = allow_different_version
        self.skip_from_release = skip_from_release
        self.skip_npm_install = skip_npm_install

    def __eq__(self, obj):
        return isinstance(obj, Repo) and obj.get_fqn() == self.get_fqn()

    def __ne__(self, obj):
        return not self == obj

    def __hash__(self) -> int:
        return hash(self.get_fqn())

    def add_sub_repo(self, sub_repo):
        self.sub_repos.append(sub_repo)
        sub_repo.is_sub_repo = True
        sub_repo.parent_repo = self

    def get_fqn(self):
        if self.is_sub_repo:
            return self.parent_repo.name + "/" + self.name
        else:
            return self.name

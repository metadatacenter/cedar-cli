from typing import List

from org.metadatacenter.model.PrePostType import PrePostType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.VersionType import VersionType


class Repo:

    def __init__(self, name: str, repo_type: RepoType, version_list: List[VersionType],
                 is_library=False, is_microservice=False, is_private=False, for_docker=False,
                 is_frontend=False,
                 allow_different_version=False, skip_from_release=False, skip_npm_install=False,
                 build_command_list: List[str] = None, server_build_command_list: List[str] = None,
                 publish_command_list: List[str] = None,
                 skip_from_default_publish=False):
        self.name = name
        self.repo_type = repo_type
        self.version_list = version_list
        self.is_library = is_library
        self.is_microservice = is_microservice
        self.is_private = is_private
        self.for_docker = for_docker
        self.is_frontend = is_frontend
        self.is_sub_repo = False
        self.sub_repos = []
        self.parent_repo = None
        self.pre_post_type: PrePostType = PrePostType.NONE
        self.allow_different_version = allow_different_version
        self.skip_from_release = skip_from_release
        self.skip_npm_install = skip_npm_install
        self.skip_from_default_publish = skip_from_default_publish
        # Shell commands that build this repo, replacing the ones its repo type implies.
        # A repo that owns its own packaging pipeline sets this so the CLI drives that
        # pipeline instead of reproducing it from outside.
        self.build_command_list = build_command_list
        # Environment-configured static payload generation for a native nginx host. This is
        # deliberately separate from the local development build, whose Gulp task stays running.
        self.server_build_command_list = server_build_command_list
        # Shell commands that publish this repo. A repo with an explicit publication pipeline uses
        # this instead of the generic commands implied by its repository type.
        self.publish_command_list = publish_command_list

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

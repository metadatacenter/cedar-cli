import copy
import os
import sys
from datetime import datetime
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.PrePostType import PrePostType
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.util.Const import Const

console = Console()


class Util(object):
    cedar_home: str = None

    cedar_release_version: str = None
    cedar_next_development_version: str = None
    release_tag_time: str = None

    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(Util, cls).__new__(cls)
        return cls.instance

    @staticmethod
    def get_wd(repo: Repo):
        return Util.cedar_home + "/" + repo.get_fqn()

    @staticmethod
    def get_flat_repo_list(repo_list):
        repos = []
        for repo in repo_list:
            repos.append(repo)
            if len(repo.sub_repos) > 0:
                for sub_repo in repo.sub_repos:
                    repos.append(sub_repo)
        return repos

    @staticmethod
    def get_flat_repo_list_pre_post(repo_list: List[Repo]) -> List[Repo]:
        """
        Returns the repos expanded with their sub-repos.
        The parent will be present twice, decorated with ``pre_post_type`` as
            ``PrePostType.PRE`` and ``PrePostType.POST``. The sub will have ``PrePostType.SUB``
        If there is no sub-repo, ``pre_post_type`` will stay ``None``
        Used for release
        :param repo_list: List of repos to be expanded
        :return:
        """
        repos = []
        for repo in repo_list:
            if len(repo.sub_repos) == 0:
                repos.append(repo)
            else:
                pre_repo = copy.copy(repo)
                pre_repo.pre_post_type = PrePostType.PRE
                repos.append(pre_repo)
                for sub_repo in repo.sub_repos:
                    sub_repo_clone = copy.copy(sub_repo)
                    sub_repo_clone.pre_post_type = PrePostType.SUB
                    repos.append(sub_repo_clone)
                post_repo = copy.copy(repo)
                post_repo.pre_post_type = PrePostType.POST
                repos.append(post_repo)
        return repos

    @classmethod
    def check_cedar_home(cls):
        if Const.CEDAR_HOME in os.environ:
            cls.cedar_home = os.environ[Const.CEDAR_HOME]
        else:
            err = 'CEDAR_HOME environment variable is not set. In order to proceed, please set it to an existing folder'
            console.print(Panel(err, title="[bold red]Error", subtitle="[bold red]cedarcli", style=Style(color="yellow")))
            sys.exit(1)

    @classmethod
    def check_release_variables(cls):
        if Const.CEDAR_RELEASE_VERSION in os.environ:
            cls.cedar_release_version = os.environ[Const.CEDAR_RELEASE_VERSION]
        else:
            err = 'CEDAR_RELEASE_VERSION environment variable is not set. In order to proceed, please set it to a valid version'
            console.print(Panel(err, title="[bold red]Error", subtitle="[bold red]cedarcli", style=Style(color="yellow")))
            sys.exit(1)

        if Const.CEDAR_NEXT_DEVELOPMENT_VERSION in os.environ:
            cls.cedar_next_development_version = os.environ[Const.CEDAR_NEXT_DEVELOPMENT_VERSION]
        else:
            err = 'CEDAR_NEXT_DEVELOPMENT_VERSION environment variable is not set. In order to proceed, please set it to a valid version'
            console.print(Panel(err, title="[bold red]Error", subtitle="[bold red]cedarcli", style=Style(color="yellow")))
            sys.exit(1)
        current_datetime = datetime.now()
        cls.release_tag_time = current_datetime.strftime("%Y%m%d-%H%M%S")

    @classmethod
    def get_release_vars(cls):
        release_version = Util.cedar_release_version
        release_branch_name = 'release/pre-' + release_version + '/' + cls.release_tag_time
        release_tag_name = 'release-' + release_version
        return release_version, release_branch_name, release_tag_name

import copy
import os
import re
import sys
from datetime import datetime
from typing import List

import rich
from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.PrePostType import PrePostType
from org.metadatacenter.model.PreReleaseBranchType import PreReleaseBranchType
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Const import Const

console = Console()


class Util(object):
    NEXT_GIT_FILE = 'next_git_repo'
    LAST_GIT_FILE = 'last_git_repo'
    LAST_PLAN_JSON_FILE = 'last_plan_content.json'
    LAST_PLAN_SCRIPT_FILE = 'last_plan_content.sh'
    LAST_RELEASE_PRE_BRANCH = 'last_release_pre_branch'
    LAST_RELEASE_POST_BRANCH = 'last_release_post_branch'
    LAST_RELEASE_TAG = 'last_release_tag'
    LAST_RELEASE_VERSION = 'last_release_version'
    LAST_RELEASE_NEXT_DEV_VERSION = 'last_release_next_dev_version'

    cedar_home: str = None
    cedar_release_version: str = None
    cedar_next_development_version: str = None
    release_tag_time: str = None
    rollback_branch: str = None
    rollback_tag: str = None

    pre_branch: str = None
    post_branch: str = None

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
    def get_release_vars(cls, branch_type: PreReleaseBranchType):
        if branch_type == PreReleaseBranchType.RELEASE:
            release_version = Util.cedar_release_version
            release_pre_branch_name = 'release/pre-' + release_version + '/' + cls.release_tag_time
            release_tag_name = 'release-' + release_version
            return release_version, release_pre_branch_name, release_tag_name
        elif branch_type == PreReleaseBranchType.NEXT_DEV:
            release_next_dev_version = Util.cedar_next_development_version
            release_post_branch_name = 'release/post-' + release_next_dev_version + '/' + cls.release_tag_time
            return release_next_dev_version, release_post_branch_name, None

    @classmethod
    def get_allow_snapshots(cls, branch_type):
        if branch_type == PreReleaseBranchType.RELEASE:
            return False
        elif branch_type == PreReleaseBranchType.NEXT_DEV:
            return True

    @classmethod
    def get_osa_script_path(cls, script_name):
        return os.path.join(os.getcwd(), 'scripts', 'osa', script_name)

    @classmethod
    def write_cedar_file(cls, file_name, content):
        file_path = cls.get_cedar_file(file_name)
        with open(file_path, "w") as file:
            file.write(content)
        return file_path

    @classmethod
    def read_cedar_file(cls, file_name):
        path = cls.get_cedar_file(file_name)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as file:
            return file.read().rstrip()

    @classmethod
    def delete_cedar_file(cls, file_name):
        path = cls.get_cedar_file(file_name)
        if os.path.exists(path):
            os.remove(path)

    @classmethod
    def get_cedar_file(cls, file_name):
        parent_path = os.path.expanduser('~/.cedar/')
        if not os.path.exists(parent_path):
            os.makedirs(parent_path)
        return os.path.join(parent_path, file_name)

    @classmethod
    def read_file(cls, file_path):
        if not os.path.exists(file_path):
            return None
        with open(file_path, 'r') as file:
            return file.read().rstrip()

    @classmethod
    def match_cedar_docker_version(cls, value):
        x = re.search("CEDAR_DOCKER_VERSION=(.*)", value)
        if x is None:
            return None
        return x.group(1)

    @classmethod
    def match_cedar_version(cls, value):
        x = re.search("ENV CEDAR_VERSION=(.*)", value, re.MULTILINE)
        if x is None:
            return None
        return x.group(1)

    @classmethod
    def match_from_metadatacenter_version(cls, value):
        x = re.search("FROM metadatacenter/(.*):(.*)", value, re.MULTILINE)
        if x is None:
            return None
        return x.group(2)

    @classmethod
    def match_image_version(cls, value):
        x = re.search("export IMAGE_VERSION=(.*)", value, re.MULTILINE)
        if x is None:
            return None
        return x.group(1)

    @classmethod
    def match_export_cedar_version(cls, value):
        x = re.search("export CEDAR_VERSION=(.*)", value, re.MULTILINE)
        if x is None:
            return None
        return x.group(1)

    @classmethod
    def write_rich_cedar_file(cls, file_name, rich_object):
        file_path = cls.get_cedar_file(file_name)
        with open(file_path, "w") as file:
            rich.print(rich_object, file=file)
        return file_path

    @classmethod
    def get_build_version(cls, task: PlanTask):
        if task.task_type == TaskType.BUILD or task.task_type == TaskType.DEPLOY:
            return os.environ[Const.CEDAR_VERSION]
        elif task.task_type == TaskType.RELEASE_PREPARE:
            return Util.cedar_release_version
        else:
            err = 'Build version not found for TaskType:' + task.task_type
            console.print(Panel(err, title="[bold red]Error", subtitle="[bold red]cedarcli", style=Style(color="yellow")))
            sys.exit(1)

    @classmethod
    def mark_rollback_branch(cls, rollback_branch: str):
        cls.rollback_branch = rollback_branch

    @classmethod
    def mark_rollback_tag(cls, rollback_tag: str):
        cls.rollback_tag = rollback_tag

    @classmethod
    def get_rollback_vars(cls):
        return cls.rollback_branch, cls.rollback_tag

    @classmethod
    def mark_pre_branch(cls, pre_branch: str):
        cls.pre_branch = pre_branch

    @classmethod
    def mark_post_branch(cls, post_branch: str):
        cls.post_branch = post_branch

    @classmethod
    def get_cleanup_vars(cls):
        return cls.pre_branch, cls.post_branch

    @classmethod
    def check_release_commit_variables(cls):
        pre_branch = Util.read_cedar_file(Util.LAST_RELEASE_PRE_BRANCH)
        post_branch = Util.read_cedar_file(Util.LAST_RELEASE_POST_BRANCH)
        tag = Util.read_cedar_file(Util.LAST_RELEASE_TAG)
        release_version = Util.read_cedar_file(Util.LAST_RELEASE_VERSION)
        next_dev_version = Util.read_cedar_file(Util.LAST_RELEASE_NEXT_DEV_VERSION)
        return pre_branch, post_branch, tag, release_version, next_dev_version

    @staticmethod
    def get_servers():
        from org.metadatacenter.util.GlobalContext import GlobalContext
        return GlobalContext.servers.map.values()

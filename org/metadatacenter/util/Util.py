import copy
import os
import re
import sys
from math import log2
from pathlib import Path
from typing import List

import rich
from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.PrePostType import PrePostType
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Const import Const

console = Console()

class Util(object):
    NEXT_GIT_FILE = 'next_git_repo'
    LAST_GIT_FILE = 'last_git_repo'
    LAST_PLAN_JSON_FILE = 'last_plan_content.json'
    LAST_PLAN_SCRIPT_FILE = 'last_plan_content.sh'

    cedar_home: str = None


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
            inferred = Path(__file__).resolve().parents[4]
            if (inferred / 'cedar-development').is_dir():
                cls.cedar_home = str(inferred)
                os.environ[Const.CEDAR_HOME] = cls.cedar_home
            else:
                err = 'CEDAR_HOME environment variable is not set and could not be inferred from the cedarcli installation'
                console.print(Panel(err, title="[bold red]Error", subtitle="[bold red]cedarcli", style=Style(color="yellow")))
                sys.exit(1)


    @classmethod

    def get_bash_script_path(cls, script_name):
        return str(Path(__file__).resolve().parents[3] / 'scripts' / 'bash' / script_name)


    @classmethod

    def get_asset_file_path(cls, asset_path: List[str]):
        return str(Path(__file__).resolve().parents[3] / 'assets' / Path(*asset_path))


    @classmethod

    def write_cedar_file(cls, file_name: str, content):
        file_path = cls.get_cedar_file(file_name)
        return cls.write_file(file_path, content)


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

    def write_file(cls, file_path: str, content):
        with open(file_path, "w") as file:
            file.write(content)
        return file_path


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
        if 'version' in task.parameters:
            return task.get_parameter('version')
        if task.task_type == TaskType.BUILD or task.task_type == TaskType.PUBLISH:
            return os.environ[Const.CEDAR_VERSION]
        else:
            err = 'Build version not found for TaskType:' + str(task.task_type)
            console.print(Panel(err, title="[bold red]Error", subtitle="[bold red]cedarcli", style=Style(color="yellow")))
            sys.exit(1)


    @staticmethod

    def get_servers():
        from org.metadatacenter.util.GlobalContext import GlobalContext
        return GlobalContext.servers.map.values()


    @staticmethod

    def format_file_size(size: int):
        units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB")
        scaling = round(log2(size) * 4) // 40
        scaling = min(len(units) - 1, scaling)
        return str(round(size / (2 ** (10 * scaling)), 2)) + ' ' + units[scaling]


    @staticmethod

    def get_repo_suffix(repo: Repo):
        root_dir = Util.get_wd(repo)
        return root_dir[len(Util.cedar_home):]

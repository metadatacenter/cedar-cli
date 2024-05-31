import os

from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.model.RepoCheckEntry import RepoCheckEntry
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()

FS_TYPE_DIR = '📁 dir'
FS_TYPE_FILE = '📄 file'
RECOGNIZED_AS_CEDAR_REPO = 'CEDAR repo'
STATUS_ICON_OK = '✅'
STATUS_ICON_UNKNOWN = '❓'
STATUS_ICON_MISSING = '❌'
STATUS_OK = 'ok'
STATUS_MISSING = 'missing'
STATUS_UNKNOWN = 'unknown'

KNOWN_FS = {
    'neo4j': 'Neo4j installation',
    'keycloak': 'Keycloak installation',
    'CEDAR_CA': 'CEDAR CA working dir',
    'log': 'CEDAR log dir',
    '.DS_Store': 'Known mac file',
    'cache': 'Known CEDAR cache dir',
    'cedar-auth.kdbx': 'CEDAR password stash',
    'export': 'Known CEDAR export dir',
    'tmp': 'Temporary dir',
    'set-env-internal.sh': 'Known CEDAR shell script',
    'set-env-external.sh': 'Known CEDAR shell script',
    'cedar-profile-native-develop.sh': 'Known CEDAR shell script'
}


class RepoWorker(Worker):
    def __init__(self):
        super().__init__()

    @staticmethod
    def repo_config():
        table = Table("Repo",
                      Column(header="Type", justify="center"),
                      Column(header="Library", justify="center"),
                      Column(header="Client", justify="center"),
                      Column(header="Microservice", justify="center"),
                      Column(header="Frontend", justify="center"),
                      Column(header="Private", justify="center"),
                      Column(header="Docker", justify="center"))
        for repo in GlobalContext.repos.get_list_all():
            RepoWorker.add_repo_list_row(repo, table)
        console.print(table)

    @staticmethod
    def add_repo_list_row(repo, table):
        is_library = STATUS_ICON_OK if repo.is_library else ""
        is_client = STATUS_ICON_OK if repo.is_client else ""
        is_microservice = STATUS_ICON_OK if repo.is_microservice else ""
        is_private = STATUS_ICON_OK if repo.is_private else ""
        for_docker = STATUS_ICON_OK if repo.for_docker else ""
        is_frontend = STATUS_ICON_OK if repo.is_frontend else ""
        name = repo.parent_repo.name + ' ⮕ ' + repo.name if repo.is_sub_repo else repo.name
        table.add_row(name, repo.repo_type, is_library, is_client, is_microservice, is_frontend, is_private, for_docker)

    @staticmethod
    def analyze_entry(fs_name, repo_map):
        recognized_as = 'unknown'
        status = STATUS_UNKNOWN
        status_icon = STATUS_ICON_UNKNOWN
        if fs_name in repo_map:
            recognized_as = RECOGNIZED_AS_CEDAR_REPO
            status = STATUS_OK
            status_icon = STATUS_ICON_OK
        if fs_name in KNOWN_FS:
            status = STATUS_OK
            status_icon = STATUS_ICON_OK
            recognized_as = KNOWN_FS[fs_name]

        return recognized_as, status, status_icon

    @staticmethod
    def check_repos():
        table = Table("Repo/File/Dir",
                      Column(header="File Type", justify="center"),
                      Column(header="Repo Type", justify="center"),
                      Column(header="Recognized as", justify="center"),
                      Column(header="Status", justify="center")
                      )

        repo_map = {}
        for repo in GlobalContext.repos.get_list_all():
            display_name = repo.get_fqn()
            is_present = RepoWorker.get_repo_dir_status(repo)
            repo_map[display_name] = RepoCheckEntry(repo, display_name, is_present)

        dir_list = os.listdir(Util.cedar_home)
        for fs_name in dir_list:
            full_path = os.path.join(Util.cedar_home, fs_name)
            file_type = FS_TYPE_DIR if os.path.isdir(full_path) else FS_TYPE_FILE
            recognized_as, status, status_icon = RepoWorker.analyze_entry(fs_name, repo_map)
            if status == 'ok':
                if fs_name in repo_map:
                    entry = repo_map[fs_name]
                else:
                    entry = RepoCheckEntry(None, fs_name, True)
                    repo_map[fs_name] = entry
                entry.set_recognized_as(recognized_as)
            else:
                entry = RepoCheckEntry(None, fs_name, True)
                repo_map[fs_name] = entry
            entry.file_type = file_type

        cnt_ok = 0
        cnt_unknown = 0
        cnt_missing = 0
        unknown_list = []
        missing_list = []

        repo_map = dict(sorted(repo_map.items()))
        for entry in repo_map.values():
            table.add_row(entry.display_name, entry.file_type, entry.repo_type, entry.recognized_as, entry.status_icon)
            if entry.status == STATUS_OK:
                cnt_ok += 1
            elif entry.status == STATUS_UNKNOWN:
                cnt_unknown +=1
                unknown_list.append(entry.display_name)
            elif entry.status == STATUS_MISSING:
                cnt_missing +=1
                missing_list.append(entry.display_name)

        caption = str(cnt_ok) + " object/files recognized"
        if cnt_unknown > 0:
            caption += "\n, [bright_red]" + str(cnt_unknown)
            caption += " unknown: [bright_yellow]" + str(unknown_list)
        if cnt_missing > 0:
            caption += "\n, [bright_red]" + str(cnt_missing)
            caption += " missing: [bright_yellow]" + str(missing_list)
        table.caption = caption

        console.print(table)

    @staticmethod
    def get_repo_dir_status(repo):
        if os.path.isdir(Util.get_wd(repo)):
            return True
        else:
            return False

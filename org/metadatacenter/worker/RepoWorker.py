import os

from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class RepoWorker(Worker):
    def __init__(self):
        super().__init__()

    def repo_list(self):
        table = Table("Repo",
                      Column(header="Type", justify="center"),
                      Column(header="Library", justify="center"),
                      Column(header="Client", justify="center"),
                      Column(header="Microservice", justify="center"),
                      Column(header="Frontend", justify="center"),
                      Column(header="Private", justify="center"),
                      Column(header="Docker", justify="center"))
        for repo in GlobalContext.repos.get_list_all():
            self.add_repo_list_row(repo, table)
        console.print(table)

    @staticmethod
    def add_repo_list_row(repo, table):
        is_library = "✅" if repo.is_library else ""
        is_client = "✅" if repo.is_client else ""
        is_microservice = "✅" if repo.is_microservice else ""
        is_private = "✅" if repo.is_private else ""
        for_docker = "✅" if repo.for_docker else ""
        is_frontend = "✅" if repo.is_frontend else ""
        name = repo.parent_repo.name + "️ ➡️  " + repo.name if repo.is_sub_repo else repo.name
        table.add_row(name, repo.repo_type, is_library, is_client, is_microservice, is_frontend, is_private, for_docker)

    def repo_status(self):
        table = Table("Repo",
                      Column(header="Type", justify="center"),
                      Column(header="Directory", justify="center"))
        cnt_ok = 0
        cnt_nok = 0
        for repo in GlobalContext.repos.get_list_all():
            ok, nok = self.add_repo_status_row(repo, table)
            cnt_ok += ok
            cnt_nok += nok

        caption = str(cnt_ok) + " repos present"
        if cnt_nok > 0:
            caption += ", [red]" + str(cnt_nok) + " missing"
        table.caption = caption
        console.print(table)

    @staticmethod
    def add_repo_status_row(repo, table):
        cnt_ok = 0
        cnt_nok = 0
        if os.path.isdir(Util.get_wd(repo)):
            dir_status = "✅"
            cnt_ok += 1
        else:
            dir_status = "❌"
            cnt_nok += 1
        name = repo.parent_repo.name + "️ ➡️  " + repo.name if repo.is_sub_repo else repo.name
        table.add_row(name, repo.repo_type, dir_status)
        return cnt_ok, cnt_nok

    def repo_report(self):
        table = Table("File/Dir",
                      Column(header="Type", justify="center"),
                      Column(header="Recognized as", justify="center"),
                      Column(header="Status", justify="center")
                      )

        repo_map = {}
        for repo in GlobalContext.repos.get_list_all():
            repo_map[repo.get_fqn()] = 1
        dir_list = os.listdir(Util.cedar_home)
        cnt_ok = 0
        cnt_nok = 0
        unknown_list = []
        for entry in dir_list:
            full_path = os.path.join(Util.cedar_home, entry)
            entry_type = '🗂️  dir' if os.path.isdir(full_path) else '📄 file'
            recognized_as, status, status_icon = self.analyze_entry(entry, repo_map)
            if status == 'ok':
                cnt_ok += 1
            else:
                cnt_nok += 1
                unknown_list.append(entry)
            table.add_row(entry, entry_type, recognized_as, status_icon)

        caption = str(cnt_ok) + " object/files recognized"
        if cnt_nok > 0:
            caption += ", [red]" + str(cnt_nok) + " unknown:"
            caption += "\n[bright_yellow]" + str(unknown_list)
        table.caption = caption

        console.print(table)

    def analyze_entry(self, entry, repo_map):
        recognized_as = 'unknown'
        status = 'unknown'
        status_icon = '❓'
        if entry in repo_map:
            recognized_as = 'CEDAR repo'
            status = 'ok'
            status_icon = "✅"
        if entry == 'neo4j':
            recognized_as = 'Neo4j installation'
            status = 'ok'
            status_icon = "✅"
        if entry == 'keycloak':
            recognized_as = 'Keycloak installation'
            status = 'ok'
            status_icon = "✅"
        if entry == 'set-env-internal.sh' or entry == 'set-env-external.sh' or entry == 'cedar-profile-native-develop.sh':
            recognized_as = 'Known CEDAR shell script'
            status = 'ok'
            status_icon = "✅"
        if entry == 'CEDAR_CA':
            recognized_as = 'CEDAR CA working dir'
            status = 'ok'
            status_icon = "✅"
        if entry == 'log':
            recognized_as = 'CEDAR log dir'
            status = 'ok'
            status_icon = "✅"
        if entry == '.DS_Store':
            recognized_as = 'Known mac file'
            status = 'ok'
            status_icon = "✅"
        if entry == 'cache':
            recognized_as = 'Known CEDAR cache dir'
            status = 'ok'
            status_icon = "✅"
        if entry == 'cedar-auth.kdbx':
            recognized_as = 'CEDAR password stash'
            status = 'ok'
            status_icon = "✅"
        if entry == 'export':
            recognized_as = 'Known CEDAR export dir'
            status = 'ok'
            status_icon = "✅"
        if entry == 'tmp':
            recognized_as = 'Temporary dir'
            status = 'ok'
            status_icon = "✅"

        return recognized_as, status, status_icon

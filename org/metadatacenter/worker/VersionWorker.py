import os

from lxml import etree
from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class VersionWorker(Worker):

    def __init__(self):
        super().__init__()

    def check(self):
        table = Table("Repo",
                      Column(header="Type", justify="center"),
                      Column(header="Directory", justify="center"))
        cnt_ok = 0
        cnt_nok = 0
        for repo in GlobalContext.repos.get_list_all():
            ok, nok = self.add_repo_status_row(repo, table)
            cnt_ok += ok
            cnt_nok += nok
            version_report = self.get_version_report(repo)

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

    def get_version_report(self, repo):

        if repo.repo_type == RepoType.JAVA_WRAPPER:
            self.analyze_java_wrapper(repo)
        elif repo.repo_type == RepoType.JAVA:
            self.analyze_java_wrapper(repo)

    def analyze_java_wrapper(self, repo):
        root_dir = Util.get_wd(repo)
        self.analyze_pom_recursively(repo, root_dir, 0)

    def analyze_pom_recursively(self, repo, root_dir, depth):
        pom_path = os.path.join(root_dir, "pom.xml")
        tree = etree.parse(pom_path)

        console.log(pom_path)

        own_version = ''

        res = tree.xpath('/x:project/x:version', namespaces={'x': 'http://maven.apache.org/POM/4.0.0'})
        if len(res) == 1:
            own_version = res[0].text
        elif depth > 0:
            own_version = 'not set'

        console.print("Own version:" + own_version)

        res = tree.xpath('/x:project/x:parent/x:version', namespaces={'x': 'http://maven.apache.org/POM/4.0.0'})
        if len(res) == 1:
            console.print("Parent version:" + res[0].text)

        if repo.name == 'cedar-parent':
            res = tree.xpath('/x:project/x:properties/x:cedar.version', namespaces={'x': 'http://maven.apache.org/POM/4.0.0'})
            if len(res) == 1:
                console.print("cedar.version:" + res[0].text)

        res = tree.xpath('/x:project/x:modules/x:module', namespaces={'x': 'http://maven.apache.org/POM/4.0.0'})
        if len(res) > 0:
            for module in res:
                console.print("Module:" + module.text)
                if not module.text.startswith('..'):
                    self.analyze_pom_recursively(repo, os.path.join(root_dir, module.text), depth + 1)

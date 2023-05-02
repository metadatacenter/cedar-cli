import os
from typing import List

from lxml import etree
from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class DirReport:
    def __init__(self, repo: Repo, dir_suffix: str, parent_version: str, own_version: str, cedar_version: str):
        self.repo = repo
        self.dir_suffix = dir_suffix
        self.parent_version = parent_version
        self.own_version = own_version
        self.cedar_version = cedar_version


class VersionWorker(Worker):

    def __init__(self):
        super().__init__()

    def check(self):
        table = Table("Repo",
                      Column(header="Dir"),
                      Column(header="Type", justify="center"),
                      Column(header="Parent version"),
                      Column(header="Own version"),
                      Column(header="Cedar version"),
                      Column(header="Status"), )
        cnt_ok = 0
        cnt_nok = 0
        report = []
        for repo in GlobalContext.repos.get_list_all():
            self.get_version_report(repo, report)

        freq = {}
        for dir in report:
            if dir.parent_version in freq:
                freq[dir.parent_version] += 1
            else:
                freq[dir.parent_version] = 1
            if dir.own_version in freq:
                freq[dir.own_version] += 1
            else:
                freq[dir.own_version] = 1

        version_candidate = max(freq, key=freq.get)
        print()

        cnt_ok = 0
        cnt_nok = 0
        for dir in report:
            if not self.eq_or_empty(dir.own_version, version_candidate) or \
                    not self.eq_or_empty(dir.parent_version, version_candidate) or \
                    not self.eq_or_empty(dir.cedar_version, version_candidate):
                dir_status = "❌"
                cnt_nok += 1
            else:
                dir_status = "✅"
                cnt_ok += 1
            table.add_row(dir.repo.name, dir.dir_suffix, dir.repo.repo_type, dir.parent_version, dir.own_version, dir.cedar_version, dir_status)

        caption = 'Target version: ' + version_candidate + '; ' + str(cnt_ok) + " versions matching"
        if cnt_nok > 0:
            caption += ", [red]" + str(cnt_nok) + " non-matching"
        table.caption = caption
        console.print(table)

    def get_version_report(self, repo, report: List[DirReport]):
        if repo.repo_type == RepoType.JAVA_WRAPPER or repo.repo_type == RepoType.JAVA:
            self.analyze_java_wrapper(repo, report)

    def analyze_java_wrapper(self, repo, report: List[DirReport]):
        root_dir = Util.get_wd(repo)
        self.analyze_pom_recursively(repo, root_dir, 0, report)

    def analyze_pom_recursively(self, repo, root_dir, depth, report: List[DirReport]):
        pom_path = os.path.join(root_dir, "pom.xml")
        tree = etree.parse(pom_path)

        console.log(pom_path)

        own_version = ''
        parent_version = ''

        res = self.get_spath(tree, '/x:project/x:version')
        if len(res) == 1:
            own_version = res[0].text
        elif depth > 0:
            own_version = ''

        res = self.get_spath(tree, '/x:project/x:parent/x:version')
        if len(res) == 1:
            parent_version = res[0].text
        else:
            parent_version = ''

        # TODO: this if should be data-driven, maybe look for it in all poms, if present. Enumerate at the top
        cedar_version = ''
        if repo.name == 'cedar-parent':
            res = self.get_spath(tree, '/x:project/x:properties/x:cedar.version')
            if len(res) == 1:
                cedar_version = res[0].text
                console.print(cedar_version)

        dir_suffix = root_dir[len(Util.cedar_home):]
        report.append(DirReport(repo, dir_suffix, parent_version, own_version, cedar_version))

        res = self.get_spath(tree, '/x:project/x:modules/x:module')
        if len(res) > 0:
            for module in res:
                if not module.text.startswith('..'):
                    self.analyze_pom_recursively(repo, os.path.join(root_dir, module.text), depth + 1, report)

    def get_spath(self, tree, path):
        return tree.xpath(path, namespaces={'x': 'http://maven.apache.org/POM/4.0.0'})

    def eq_or_empty(self, version, reference_version):
        return version == reference_version or version == ""

import os

import requests
from lxml import etree
from lxml.etree import Element
from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.model.ArtifactEntryReport import ArtifactEntryReport
from org.metadatacenter.model.ArtifactReport import ArtifactReport
from org.metadatacenter.model.ArtifactStatus import ArtifactStatus
from org.metadatacenter.model.ArtifactType import ArtifactType
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class ArtifactsWorker(Worker):

    def __init__(self):
        super().__init__()

    def check_artifacts(self):
        table = Table(Column(header="Repo"),
                      Column(header="Path"),
                      Column(header="RepoType", justify="center"),
                      Column(header="ArtifactType", justify="center"),
                      Column(header="URL"),
                      Column(header="Response Code"),
                      Column(header="Size", justify="right"),
                      Column(header="Status"))

        report = ArtifactReport()
        for repo in GlobalContext.repos.get_list_all():
            self.get_artifact_report(repo, report)

        for entry in report.entries:
            table.add_row(entry.repo.name, entry.dir_suffix, entry.repo.repo_type, entry.repo.artifact_type, entry.url,
                          str(entry.response_code) if (entry.response_code != -1) else '',
                          Util.format_file_size(int(entry.size)) if (entry.size != -1) else '',
                          entry.status_string)
            table.add_section()

        table.caption = report.get_caption()
        console.print(table)
        Util.write_rich_cedar_file('last_artifacts_check.rich.txt', table)

    def get_artifact_report(self, repo: Repo, report: ArtifactReport):
        if repo.artifact_type == ArtifactType.NONE:
            ArtifactsWorker.mark_none(repo, report)
        elif repo.artifact_type == ArtifactType.NPM:
            ArtifactsWorker.analyze_npm(repo, report)
        elif repo.artifact_type == ArtifactType.MAVEN:
            ArtifactsWorker.analyze_maven(repo, report)

    @staticmethod
    def mark_none(repo, report: ArtifactReport):
        root_dir = Util.get_wd(repo)
        dir_suffix = root_dir[len(Util.cedar_home):]
        entry = ArtifactEntryReport(repo, dir_suffix, '')
        entry.set_status(ArtifactStatus.NOT_NEEDED)
        report.add_entry(entry)

    @staticmethod
    def lookup_entry(repo, dir_suffix, url, report):
        entry = ArtifactEntryReport(repo, dir_suffix, url)
        console.log("Check url:" + url)

        entry.set_status(ArtifactStatus.ERROR)

        response = requests.head(url)
        entry.response_code = response.status_code
        if entry.response_code == 200:
            entry.size = response.headers['Content-Length']
            entry.set_status(ArtifactStatus.FOUND)
        report.add_entry(entry)

    @staticmethod
    def analyze_npm(repo, report: ArtifactReport):
        base = "https://nexus.bmir.stanford.edu/repository/npm-cedar/"
        # TODO: handle CEDAR_VERSION at a central point
        version = os.environ[Const.CEDAR_VERSION]
        url = base + repo.name + '/-/' + repo.name + '-' + version + '.tgz'
        dir_suffix = Util.get_repo_suffix(repo)
        ArtifactsWorker.lookup_entry(repo, dir_suffix, url, report)

    @staticmethod
    def analyze_maven(repo, report: ArtifactReport):
        root_dir = Util.get_wd(repo)
        ArtifactsWorker.analyze_pom_recursively(repo, root_dir, 0, report)

    @staticmethod
    def analyze_pom_recursively(repo: Repo, root_dir: str, depth: int, report: ArtifactReport):
        pom_path = os.path.join(root_dir, Const.FILE_POM_XML)
        tree = etree.parse(pom_path)

        artifact_id = None
        packaging = 'jar'

        res = ArtifactsWorker.get_spath(tree, '/x:project/x:artifactId')
        if len(res) == 1:
            artifact_id = res[0].text
        else:
            console.log("Unable to detect artifactId for:" + Util.get_repo_suffix(repo))

        res = ArtifactsWorker.get_spath(tree, '/x:project/x:packaging')
        if len(res) == 1:
            packaging = res[0].text
        else:
            console.log("Unable to detect packaging for:" + Util.get_repo_suffix(repo))

        # TODO: handle CEDAR_VERSION at a central point
        version = os.environ[Const.CEDAR_VERSION]
        entry_added = False
        dir_suffix = Util.get_repo_suffix(repo)
        url = ''

        if "SNAPSHOT" in version:
            base = "https://nexus.bmir.stanford.edu/repository/snapshots/org/metadatacenter/"
            meta_url = base + artifact_id + '/' + version + '/' + 'maven-metadata.xml'
            meta_response = requests.get(meta_url)
            meta_response_code = meta_response.status_code
            console.log("Meta url:" + meta_url)
            if meta_response_code == 200:
                meta_content = meta_response.content
                tree = etree.fromstring(meta_content)
                snapshot_version_0: Element = \
                    ArtifactsWorker.get_nexus_spath(tree, '/metadata/versioning/snapshotVersions/snapshotVersion[1]/value')[0].text

                url = base + artifact_id + '/' + version + '/' + artifact_id + '-' + snapshot_version_0 + '.' + packaging
                ArtifactsWorker.lookup_entry(repo, dir_suffix, url, report)
                entry_added = True
        else:
            base = "https://nexus.bmir.stanford.edu/repository/releases/org/metadatacenter/"
            url = base + artifact_id + '/' + version + '/' + artifact_id + '-' + version + '.' + packaging
            ArtifactsWorker.lookup_entry(repo, dir_suffix, url, report)
            entry_added = True

        if not entry_added:
            entry = ArtifactEntryReport(repo, dir_suffix, url)
            entry.set_status(ArtifactStatus.ERROR)
            report.add_entry(entry)

        res = ArtifactsWorker.get_spath(tree, '/x:project/x:modules/x:module')
        if len(res) > 0:
            for module in res:
                if not module.text.startswith('..'):
                    ArtifactsWorker.analyze_pom_recursively(repo, os.path.join(root_dir, module.text), depth + 1, report)

    @staticmethod
    def get_spath(tree, path):
        return tree.xpath(path, namespaces={'x': 'http://maven.apache.org/POM/4.0.0'})

    @staticmethod
    def get_nexus_spath(tree, path):
        return tree.xpath(path)

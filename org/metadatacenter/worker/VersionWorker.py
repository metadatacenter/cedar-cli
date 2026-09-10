import json
import os

from jsonpath_ng import parse
from lxml import etree
from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.model.ArtifactEntryReport import ArtifactEntryReport
from org.metadatacenter.model.ArtifactStatus import ArtifactStatus
from org.metadatacenter.model.RepoRelation import RepoRelation
from org.metadatacenter.model.RepoRelationType import RepoRelationType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.VersionReport import VersionReport
from org.metadatacenter.model.VersionType import VersionType
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.GitSync import GitSync
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class VersionWorker(Worker):

    def __init__(self):
        super().__init__()

    def check_versions(self, by_file: bool = False):
        report = VersionReport()
        for repo in GlobalContext.repos.get_list_all():
            self.get_version_report(repo, report)

        GitSync.clear_cache()
        for entry in report.entries:
            entry.sync = GitSync.state_for_dir(VersionWorker.entry_dir(entry))

        report.summarize()

        table = self.build_file_table(report) if by_file else self.build_repo_table(report)
        table.caption = report.get_caption()
        console.print(table)
        for line in report.get_remedy_lines():
            console.print(line)
        Util.write_rich_cedar_file('last_version_check.rich.txt', table)
        return 1 if report.cnt_nok else 0

    @staticmethod
    def entry_dir(entry) -> str:
        """The working directory an entry was read from, or an unresolvable path if there is none."""
        if Util.cedar_home is None:
            return ''
        return os.path.join(Util.cedar_home, entry.dir_suffix.lstrip('/'))

    @staticmethod
    def build_file_table(report: VersionReport) -> Table:
        table = Table("Repo",
                      Column(header="Dir"),
                      Column(header="Type", justify="center"),
                      Column(header="File"),
                      Column(header="Version type"),
                      Column(header="Version value"),
                      Column(header="Sync"),
                      Column(header="Status"))
        last_repo_name = None
        for entry in report.entries:
            if last_repo_name != entry.repo.name:
                table.add_section()
            table.add_row(entry.repo.name, entry.dir_suffix, str(entry.repo.repo_type), entry.file_name,
                          str(entry.version_type), entry.version, entry.sync.describe(), entry.status)
            last_repo_name = entry.repo.name
        return table

    @staticmethod
    def build_repo_table(report: VersionReport) -> Table:
        """
        One row per repository, because a whole repository at one version is one finding.

        The per-file view answers a different question, and `--by-file` asks it: which file inside a
        repository disagrees with its siblings.
        """
        table = Table("Repo",
                      Column(header="Type", justify="center"),
                      Column(header="Files", justify="right"),
                      Column(header="Version(s)"),
                      Column(header="Sync"),
                      Column(header="Status"))
        disagreeing = set(report.repos_disagreeing_internally())
        for name in VersionWorker.repo_names_in_order(report):
            entries = [entry for entry in report.entries if entry.repo.name == name]
            versions = []
            for entry in entries:
                if entry.version and entry.version not in versions:
                    versions.append(entry.version)
            table.add_row(name,
                          str(entries[0].repo.repo_type),
                          str(len(entries)),
                          ", ".join(versions),
                          entries[0].sync.describe(),
                          VersionWorker.rollup_status(entries, name in disagreeing))
        return table

    @staticmethod
    def repo_names_in_order(report: VersionReport):
        names = []
        for entry in report.entries:
            if entry.repo.name not in names:
                names.append(entry.repo.name)
        return names

    @staticmethod
    def rollup_status(entries, disagrees_internally: bool) -> str:
        """
        The worst thing true of a repository, with one exception that outranks the rest.

        Files inside one repository declaring different versions is a half-applied bump. No pull
        fixes it and it is not ordinary drift, so it is reported as itself rather than folded into
        whichever status a member file happened to get.
        """
        if disagrees_internally:
            return "⚠️"
        for status in ("❌", "⏳", "👍"):
            if any(entry.status == status for entry in entries):
                return status
        return "✅"

    def get_version_report(self, repo, report: VersionReport):
        if repo.repo_type == RepoType.JAVA_WRAPPER or repo.repo_type == RepoType.JAVA:
            self.analyze_java_wrapper(repo, report)
        elif repo.repo_type == RepoType.ANGULAR_JS or repo.repo_type == RepoType.ANGULAR:
            self.analyze_angular_js(repo, report)
        elif repo.repo_type == RepoType.EMBER or repo.repo_type == RepoType.REACT:
            self.analyze_npm_package(repo, report)
        elif repo.repo_type == RepoType.ANGULAR_DIST:
            self.analyze_angular_dist(repo, report)
        elif repo.repo_type == RepoType.TYPESCRIPT:
            self.analyze_typescript(repo, report)
        elif repo.repo_type == RepoType.MULTI or repo.repo_type == RepoType.PYTHON or repo.repo_type == RepoType.MKDOCS \
                or repo.repo_type == RepoType.CONTENT_DELIVERY or repo.repo_type == RepoType.PHP or repo.repo_type == RepoType.MISC:
            VersionWorker.mark_empty(repo, report)
        elif repo.repo_type == RepoType.DOCKER_BUILD:
            VersionWorker.analyze_docker_build(repo, report)
        elif repo.repo_type == RepoType.DOCKER_DEPLOY:
            VersionWorker.analyze_docker_deploy(repo, report)
        elif repo.repo_type == RepoType.DEVELOPMENT:
            VersionWorker.analyze_development(repo, report)
        else:
            VersionWorker.mark_unknown(repo, report)

    def analyze_java_wrapper(self, repo, report: VersionReport):
        root_dir = Util.get_wd(repo)
        self.analyze_pom_recursively(repo, root_dir, 0, report)

    def analyze_pom_recursively(self, repo, root_dir, depth, report: VersionReport):
        pom_path = os.path.join(root_dir, Const.FILE_POM_XML)
        url = ''
        try:
            tree = etree.parse(pom_path)
        except Exception as e:
            dir_suffix = Util.get_repo_suffix(repo)
            entry = ArtifactEntryReport(repo, dir_suffix, url)
            entry.set_status(ArtifactStatus.ERROR)
            report.add(repo, dir_suffix, Const.FILE_POM_XML, VersionType.POM_OWN, 'MISSING')
            return

        dir_suffix = root_dir[len(Util.cedar_home):]

        res = self.get_spath(tree, '/x:project/x:version')
        if len(res) == 1:
            report.add(repo, dir_suffix, Const.FILE_POM_XML, VersionType.POM_OWN, res[0].text)

        res = self.get_spath(tree, '/x:project/x:parent/x:version')
        if len(res) == 1:
            report.add(repo, dir_suffix, Const.FILE_POM_XML, VersionType.POM_PARENT, res[0].text)

        res = self.get_spath(tree, '/x:project/x:properties/x:cedar.version')
        if len(res) == 1:
            report.add(repo, dir_suffix, Const.FILE_POM_XML, VersionType.POM_PROPERTIES, res[0].text)

        res = self.get_spath(tree, '/x:project/x:modules/x:module')
        if len(res) > 0:
            for module in res:
                if not module.text.startswith('..'):
                    self.analyze_pom_recursively(repo, os.path.join(root_dir, module.text), depth + 1, report)

    @staticmethod
    def get_spath(tree, path):
        return tree.xpath(path, namespaces={'x': 'http://maven.apache.org/POM/4.0.0'})

    @staticmethod
    def get_json_path(json_data, json_path):
        jsonpath_expression = parse(json_path)
        match = jsonpath_expression.find(json_data)
        if len(match) == 1:
            return match[0].value

    @staticmethod
    def mark_unknown(repo, report):
        dir_suffix = Util.get_repo_suffix(repo)
        report.add(repo, dir_suffix, '', VersionType.UNKNOWN, '')

    @staticmethod
    def mark_empty(repo, report: VersionReport):
        dir_suffix = Util.get_repo_suffix(repo)
        report.add(repo, dir_suffix, '', VersionType.EMPTY, '')

    def analyze_angular_js(self, repo, report: VersionReport):
        root_dir = Util.get_wd(repo)
        dir_suffix = Util.get_repo_suffix(repo)

        self.analyze_package_and_lock(repo, report, root_dir, dir_suffix)

        source_of_relations = GlobalContext.repos.get_relations(repo, RepoRelationType.IS_SOURCE_OF)
        for source_of_relation in source_of_relations:
            if (RepoRelation.TARGET_SUB_FOLDER in source_of_relation.parameters):
                dist_subfolder = source_of_relation.parameters[RepoRelation.TARGET_SUB_FOLDER]

                if VersionType.DIST_NPM_PACKAGE_OWN in repo.version_list:
                    package_json_path = os.path.join(root_dir, dist_subfolder, Const.FILE_PACKAGE_JSON)
                    with open(package_json_path, 'r') as json_file:
                        json_data = json.load(json_file)
                        version = self.get_json_path(json_data, '$.version')
                        report.add(repo, dir_suffix, Const.FILE_PACKAGE_JSON, VersionType.DIST_NPM_PACKAGE_OWN, version)

                if VersionType.DIST_NPM_PACKAGE_LOCK_OWN in repo.version_list or VersionType.DIST_NPM_PACKAGE_LOCK_PACKAGES_OWN in repo.version_list:
                    package_json_lock_path = os.path.join(root_dir, dist_subfolder, Const.FILE_PACKAGE_LOCK_JSON)
                    with open(package_json_lock_path, 'r') as json_file:
                        json_data = json.load(json_file)

                        if VersionType.PACKAGE_LOCK_OWN in repo.version_list:
                            version = self.get_json_path(json_data, '$.version')
                            report.add(repo, dir_suffix, Const.FILE_PACKAGE_LOCK_JSON, VersionType.DIST_NPM_PACKAGE_LOCK_OWN, version)

                        if VersionType.PACKAGE_LOCK_PACKAGES_OWN in repo.version_list:
                            version_pack = self.get_json_path(json_data, '$.packages[""].version')
                            report.add(repo, dir_suffix, Const.FILE_PACKAGE_LOCK_JSON, VersionType.DIST_NPM_PACKAGE_LOCK_PACKAGES_OWN, version_pack)

    def analyze_angular_dist(self, repo, report: VersionReport):
        root_dir = Util.get_wd(repo)
        dir_suffix = root_dir[len(Util.cedar_home):]

        self.analyze_package_and_lock(repo, report, root_dir, dir_suffix)

    def analyze_typescript(self, repo, report: VersionReport):
        root_dir = Util.get_wd(repo)
        dir_suffix = root_dir[len(Util.cedar_home):]

        self.analyze_package_and_lock(repo, report, root_dir, dir_suffix)

    def analyze_npm_package(self, repo, report: VersionReport):
        root_dir = Util.get_wd(repo)
        dir_suffix = Util.get_repo_suffix(repo)

        self.analyze_package_and_lock(repo, report, root_dir, dir_suffix)

    @staticmethod
    def analyze_docker_build(repo, report: VersionReport):
        root_dir = Util.get_wd(repo)
        root_dir_suffix = Util.get_repo_suffix(repo)

        dir_list = os.listdir(root_dir)
        for entry in dir_list:
            full_dir_path = os.path.join(root_dir, entry)
            full_dir_suffix = full_dir_path[len(Util.cedar_home):]

            docker_path = os.path.join(full_dir_path, Const.FILE_DOCKER)
            if os.path.isfile(docker_path):
                docker_content = Util.read_file(docker_path)
                docker_version = Util.match_cedar_version(docker_content)
                if docker_version is not None and not VersionWorker.is_dynamic_version(docker_version):
                    report.add(repo, full_dir_suffix, Const.FILE_DOCKER, VersionType.ENV_CEDAR_VERSION, docker_version)

                docker_version = Util.match_from_metadatacenter_version(docker_content)
                if docker_version is not None:
                    report.add(repo, full_dir_suffix, Const.FILE_DOCKER, VersionType.DOCKER_FROM_VERSION, docker_version)

        docker_path = os.path.join(root_dir, Const.FILE_BIN_IMAGE_BASE)
        docker_content = Util.read_file(docker_path)
        docker_version = Util.match_image_version(docker_content)
        if docker_version is not None:
            report.add(repo, root_dir_suffix, Const.FILE_BIN_IMAGE_BASE, VersionType.IMAGE_VERSION, docker_version)

    @staticmethod
    def is_dynamic_version(version):
        """Return true for Dockerfile values supplied by the immutable build train."""
        normalized = version.strip().strip('"\'')
        return normalized in {'$CEDAR_MAVEN_VERSION', '${CEDAR_MAVEN_VERSION}'}

    @staticmethod
    def analyze_docker_deploy(repo, report: VersionReport):
        root_dir = Util.get_wd(repo)

        dir_list = os.listdir(root_dir)
        for entry in dir_list:
            full_dir_path = os.path.join(root_dir, entry)
            full_dir_suffix = full_dir_path[len(Util.cedar_home):]

            env_path = os.path.join(full_dir_path, Const.FILE_ENV)
            if os.path.isfile(env_path):
                env_content = Util.read_file(env_path)
                docker_version = Util.match_cedar_docker_version(env_content)
                report.add(repo, full_dir_suffix, Const.FILE_ENV, VersionType.ENV_CEDAR_DOCKER_VERSION, docker_version)

    @staticmethod
    def analyze_development(repo, report: VersionReport):
        root_dir = Util.get_wd(repo)
        root_dir_suffix = Util.get_repo_suffix(repo)

        docker_path = os.path.join(root_dir, Const.FILE_BIN_UTIL_SET_ENV_GENERIC)
        docker_content = Util.read_file(docker_path)
        docker_version = Util.match_export_cedar_version(docker_content)
        if docker_version is not None:
            report.add(repo, root_dir_suffix, Const.FILE_BIN_UTIL_SET_ENV_GENERIC, VersionType.ENV_CEDAR_VERSION, docker_version)

    def analyze_package_and_lock(self, repo, report: VersionReport, root_dir: str, dir_suffix: str):
        if VersionType.PACKAGE_OWN in repo.version_list:
            package_json_path = os.path.join(root_dir, Const.FILE_PACKAGE_JSON)
            with open(package_json_path, 'r') as json_file:
                json_data = json.load(json_file)
                version = self.get_json_path(json_data, '$.version')
                report.add(repo, dir_suffix, Const.FILE_PACKAGE_JSON, VersionType.PACKAGE_OWN, version)

        if VersionType.PACKAGE_LOCK_OWN in repo.version_list or VersionType.PACKAGE_LOCK_PACKAGES_OWN in repo.version_list:
            package_json_lock_path = os.path.join(root_dir, Const.FILE_PACKAGE_LOCK_JSON)
            with open(package_json_lock_path, 'r') as json_file:
                json_data = json.load(json_file)

                if VersionType.PACKAGE_LOCK_OWN in repo.version_list:
                    version = self.get_json_path(json_data, '$.version')
                    report.add(repo, dir_suffix, Const.FILE_PACKAGE_LOCK_JSON, VersionType.PACKAGE_LOCK_OWN, version)

                if VersionType.PACKAGE_LOCK_PACKAGES_OWN in repo.version_list:
                    version_pack = self.get_json_path(json_data, '$.packages[""].version')
                    report.add(repo, dir_suffix, Const.FILE_PACKAGE_LOCK_JSON, VersionType.PACKAGE_LOCK_PACKAGES_OWN, version_pack)

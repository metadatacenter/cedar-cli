import json
import os

from jsonpath_ng import parse
from lxml import etree
from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.model.RepoRelation import RepoRelation
from org.metadatacenter.model.RepoRelationType import RepoRelationType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.VersionReport import VersionReport
from org.metadatacenter.model.VersionType import VersionType
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class VersionWorker(Worker):

    def __init__(self):
        super().__init__()

    def check_versions(self):
        table = Table("Repo",
                      Column(header="Dir"),
                      Column(header="Type", justify="center"),
                      Column(header="File"),
                      Column(header="Version type"),
                      Column(header="Version value"),
                      Column(header="Status"))

        report = VersionReport()
        for repo in GlobalContext.repos.get_list_all():
            self.get_version_report(repo, report)

        report.summarize()

        last_repo_name = None
        for entry in report.entries:
            if last_repo_name != entry.repo.name:
                table.add_section()
            table.add_row(entry.repo.name, entry.dir_suffix, str(entry.repo.repo_type), entry.file_name,
                          str(entry.version_type), entry.version, entry.status)
            last_repo_name = entry.repo.name

        table.caption = report.get_caption()
        console.print(table)
        Util.write_rich_cedar_file('last_version_check.rich.txt', table)

    def get_version_report(self, repo, report: VersionReport):
        if repo.repo_type == RepoType.JAVA_WRAPPER or repo.repo_type == RepoType.JAVA:
            self.analyze_java_wrapper(repo, report)
        elif repo.repo_type == RepoType.ANGULAR_JS or repo.repo_type == RepoType.ANGULAR:
            self.analyze_angular_js(repo, report)
        elif repo.repo_type == RepoType.ANGULAR_DIST:
            self.analyze_angular_dist(repo, report)
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
        tree = etree.parse(pom_path)

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

        source_of_relations = GlobalContext.repos.get_relations(repo, RepoRelationType.IS_SOURCE_OF)
        for source_of_relation in source_of_relations:
            print(source_of_relation.source_repo.name + ' => ' + source_of_relation.target_repo.name)
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

        package_json_path = os.path.join(root_dir, Const.FILE_PACKAGE_JSON)
        with open(package_json_path, 'r') as json_file:
            json_data = json.load(json_file)
            version = self.get_json_path(json_data, '$.version')
            report.add(repo, dir_suffix, Const.FILE_PACKAGE_JSON, VersionType.PACKAGE_OWN, version)

        package_json_lock_path = os.path.join(root_dir, Const.FILE_PACKAGE_LOCK_JSON)
        with open(package_json_lock_path, 'r') as json_file:
            json_data = json.load(json_file)

            version = self.get_json_path(json_data, '$.version')
            report.add(repo, dir_suffix, Const.FILE_PACKAGE_LOCK_JSON, VersionType.PACKAGE_LOCK_OWN, version)

            version_pack = self.get_json_path(json_data, '$.packages[""].version')
            report.add(repo, dir_suffix, Const.FILE_PACKAGE_LOCK_JSON, VersionType.PACKAGE_LOCK_PACKAGES_OWN, version_pack)

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
                if docker_version is not None:
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

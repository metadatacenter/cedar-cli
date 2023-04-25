from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType


class ShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def maven_clean_install_skip_tests(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Maven clean install skip tests", TaskType.SHELL, repo)
        task.command_list = ['mvn clean install -DskipTests']
        return task

    @classmethod
    def npm_install_legacy_ng_build(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM install, NG build", TaskType.SHELL, repo)
        task.command_list = ['npm install --legacy-peer-deps', 'ng build --configuration=production']
        return task

    @classmethod
    def npm_install(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM install", TaskType.SHELL, repo)
        task.command_list = ['npm install']
        return task

    @classmethod
    def copy_src_content_to_dest(cls, source_path: str, target_path: str, repo: Repo) -> PlanTask:
        task = PlanTask("Copy source content into destination", TaskType.SHELL, repo)
        command = f"cp -a {source_path}/* {target_path}/."
        task.command_list = [command]
        return task

    @classmethod
    def maven_deploy_skip_tests(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Maven deploy skip tests", TaskType.SHELL, repo)
        task.command_list = ['mvn deploy -DskipTests']
        return task

    @classmethod
    def npm_install_legacy_ng_build_publish(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM install, NG build, NPM publish", TaskType.SHELL, repo)
        task.command_list = ['npm install --legacy-peer-deps', 'ng build --configuration=production', 'npm publish']
        return task

    @classmethod
    def npm_install_publish(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM install, NPM publish", TaskType.SHELL, repo)
        task.command_list = ['npm install', 'npm publish']
        return task

    @classmethod
    def release_prepare_java_wrapper(cls, repo: Repo) -> PlanTask:
        from org.metadatacenter.util.GlobalContext import GlobalContext
        task = PlanTask("Prepare release of java wrapper", TaskType.SHELL, repo)

        CEDAR_RELEASE_VERSION = '2.6.26'
        release_branch_name = 'release/pre-' + CEDAR_RELEASE_VERSION
        release_tag_name = 'release-' + CEDAR_RELEASE_VERSION

        replace_version_command_1 = ''
        replace_version_command_2 = ''
        replace_version_command_3 = ''
        build_command = ''
        if repo in GlobalContext.repos.get_parent():
            replace_version_command_1 = "xmlstarlet ed -L -u '_:project/_:version' -v '" + CEDAR_RELEASE_VERSION + "' pom.xml"
            replace_version_command_2 = "xmlstarlet ed -L -u '_:project/_:parent/_:version' -v '" + CEDAR_RELEASE_VERSION + "' pom.xml"
            replace_version_command_3 = "xmlstarlet ed -L -u '_:project/_:properties/_:cedar.version' -v '" + CEDAR_RELEASE_VERSION + "' pom.xml"
            build_command = 'mvn clean install -DskipTests'

        task.command_list = [
            'echo "Create pre release branch"',
            'git checkout develop',
            'git pull origin develop',
            'git checkout -b ' + release_branch_name,

            'echo "Update to next release version"',
            replace_version_command_1,
            replace_version_command_2,
            replace_version_command_3,

            'echo "Build release version"',
            build_command,

            'echo "Commit changes after build"',
            'git add .',
            'git commit -a -m "Produce release version of component"',
            'git push origin ' + release_branch_name,

            'echo "Tag repo with release version"',
            'git tag "' + release_tag_name + '"',
            'git push origin "' + release_tag_name + '"'
        ]
        return task

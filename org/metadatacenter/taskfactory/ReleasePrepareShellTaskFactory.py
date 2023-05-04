from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Util import Util


class ReleasePrepareShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def prepare_java(cls, repo: Repo) -> PlanTask:
        from org.metadatacenter.util.GlobalContext import GlobalContext
        task = PlanTask("Prepare release of java wrapper", TaskType.SHELL, repo)

        release_version, release_branch_name, release_tag_name = Util.get_release_vars()

        replace_version_commands = []
        build_command = ''
        if repo in GlobalContext.repos.get_parent():
            replace_version_commands = [
                "      xmlstarlet ed -L -u '_:project/_:version' -v '" + release_version + "' pom.xml",
                "      xmlstarlet ed -L -u '_:project/_:properties/_:cedar.version' -v '" + release_version + "' pom.xml"]
            build_command = '      mvn clean install -DskipTests'
        elif repo.repo_type == RepoType.JAVA_WRAPPER or repo.repo_type == RepoType.JAVA:
            replace_version_commands = [
                '      mvn versions:set -DnewVersion="' + release_version + '" -DupdateMatchingVersions=false',
                '      mvn versions:update-parent versions:update-child-modules',
                '      mvn -DallowSnapshots=false versions:update-properties']
            build_command = '      mvn clean install -DskipTests'

        task.command_list = [
            *cls.macro_create_pre_release_branch(),
            'echo "Update to next release version"',
            *replace_version_commands,
            'echo "Build release version"',
            build_command,
            *cls.macro_commit_changes(),
            *cls.macro_tag_repo()
        ]
        return task

    @classmethod
    def prepare_angular_js(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angularJS project", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_create_pre_release_branch(),
            *cls.macro_update_package_json_and_travis(),
            *cls.macro_build_angular_js(),
            *cls.macro_commit_changes(),
            *cls.macro_tag_repo()
        ]
        return task

    @classmethod
    def prepare_angular_src(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular standalone project", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_create_pre_release_branch(),
            *cls.macro_update_package_json_and_travis(),
            *cls.macro_update_index_html_version_numbers(),
            *cls.macro_build_angular(),
            *cls.macro_commit_changes(),
            *cls.macro_tag_repo()
        ]
        return task

    @classmethod
    def prepare_angular_src_sub(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular sub-project", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_update_package_json_and_travis(),
            *cls.macro_update_index_html_version_numbers(),
            *cls.macro_build_angular(),
        ]
        return task

    @classmethod
    def prepare_angular_dist_sub(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular dist sub-project", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_update_package_json_and_travis(),
        ]
        return task

    @classmethod
    def prepare_plain_sub(cls, repo):
        task = PlanTask("Prepare release of plain sub repo", TaskType.SHELL, repo)
        task.command_list = [
        ]
        return task

    @classmethod
    def prepare_multi_pre(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of multi directory project", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_create_pre_release_branch()
        ]
        return task

    @classmethod
    def prepare_multi_post(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Wrap up release of multi directory project", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_commit_changes(),
            *cls.macro_tag_repo()
        ]
        return task

    @classmethod
    def prepare_plain(cls, repo):
        task = PlanTask("Prepare release of plain repo", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_create_pre_release_branch(),
            *cls.macro_commit_changes(),
            *cls.macro_tag_repo()
        ]
        return task

    @classmethod
    def prepare_development(cls, repo):
        task = PlanTask("Prepare release of development repo", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_create_pre_release_branch(),
            *cls.macro_update_development_cedar_version(),
            *cls.macro_commit_changes(),
            *cls.macro_tag_repo()
        ]
        return task

    @classmethod
    def prepare_docker_deploy(cls, repo):
        task = PlanTask("Prepare release of Docker deploy repo", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_create_pre_release_branch(),
            *cls.macro_update_env_cedar_docker_version(),
            *cls.macro_commit_changes(),
            *cls.macro_tag_repo()
        ]
        return task

    @classmethod
    def prepare_docker_build(cls, repo):
        task = PlanTask("Prepare release of Docker build repo", TaskType.SHELL, repo)
        task.command_list = [
            *cls.macro_create_pre_release_branch(),
            *cls.macro_update_docker_build_versions(),
            *cls.macro_commit_changes(),
            *cls.macro_tag_repo()
        ]
        return task

    @classmethod
    def macro_tag_repo(cls):
        release_version, release_branch_name, release_tag_name = Util.get_release_vars()
        return ('echo "Tag repo with release version"',
                '      git tag "' + release_tag_name + '"',
                '      git push origin "' + release_tag_name + '"')

    @classmethod
    def macro_create_pre_release_branch(cls):
        release_version, release_branch_name, release_tag_name = Util.get_release_vars()
        return ('echo "Create pre-release branch"',
                '      git checkout develop',
                '      git pull origin develop',
                '      git checkout -b ' + release_branch_name)

    @classmethod
    def macro_commit_changes(cls):
        release_version, release_branch_name, release_tag_name = Util.get_release_vars()
        return ('echo "Commit changes after build"',
                '      git add .',
                '      git commit -a -m "Produce release version of component"',
                '      git push origin ' + release_branch_name)

    @classmethod
    def macro_update_index_html_version_numbers(cls):
        release_version, release_branch_name, release_tag_name = Util.get_release_vars()
        return ('echo "Update openview index.html"',
                "      sed -i '' 's/\/cedar-form-.*\.js/\/cedar-form-'" + release_version + "'\.js/g' src/index.html",
                "      sed -i '' 's/\/cedar-embeddable-editor-.*\.js/\/cedar-embeddable-editor-'" + release_version + "'\.js/g' src/index.html")

    @classmethod
    def macro_update_package_json_and_travis(cls):
        release_version, release_branch_name, release_tag_name = Util.get_release_vars()
        return ('echo "Update to next release version"',
                "      jq '.version=\"'" + release_version + "'\"' package.json | sponge package.json",
                "      sed -i '' 's/- CEDAR_VERSION\s*=.*\".*\"/- CEDAR_VERSION=\"'" + release_version + "'\"/g' .travis.yml")

    @classmethod
    def macro_update_development_cedar_version(cls):
        release_version, release_branch_name, release_tag_name = Util.get_release_vars()
        return ('echo "Update to next release version"',
                "      sed -i '' 's/^export CEDAR_VERSION=.*$/export CEDAR_VERSION=\"'" + release_version + "'\"/' ./bin/util/set-env-generic.sh")

    @classmethod
    def macro_update_env_cedar_docker_version(cls):
        release_version, release_branch_name, release_tag_name = Util.get_release_vars()
        return ('echo "Update to next release version"',
                "      find . -name .env -exec sed -i '' 's/^CEDAR_DOCKER_VERSION=.*$/export CEDAR_DOCKER_VERSION=\"'" + release_version + "'\"/' {} \; -print")

    @classmethod
    def macro_update_docker_build_versions(cls):
        release_version, release_branch_name, release_tag_name = Util.get_release_vars()
        return ('echo "Update to next release version"',
                "      find . -name Dockerfile -exec sed -i '' 's/^FROM metadatacenter\/cedar-microservice:.*$/FROM metadatacenter\/cedar-microservice:'" + release_version + "'/' {} \; -print",
                "      find . -name Dockerfile -exec sed -i '' 's/^FROM metadatacenter\/cedar-java:.*$/FROM metadatacenter\/cedar-java:'" + release_version + "'/' {} \; -print",
                "      find . -name Dockerfile -exec sed -i '' 's/^ENV CEDAR_VERSION=.*$/ENV CEDAR_VERSION=\"'" + release_version + "'\"/' {} \; -print",
                "      sed -i '' 's/^export IMAGE_VERSION=.*$/export IMAGE_VERSION=\"'" + release_version + "'\"/' ./bin/cedar-images-base.sh"
                )

    @classmethod
    def macro_build_angular(cls):
        return ('echo "Build release version"',
                '      npm install --legacy-peer-deps',
                '      ng build --configuration=production')

    @classmethod
    def macro_build_angular_js(cls):
        return ('echo "Build release version"',
                '      npm install')

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
        task = PlanTask("Prepare release of java wrapper", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_java_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_java_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_java_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        from org.metadatacenter.util.GlobalContext import GlobalContext
        replace_version_commands = []
        build_command = ''
        if repo in GlobalContext.repos.get_parent():
            replace_version_commands = [
                "      xmlstarlet ed -L -u '_:project/_:version' -v '" + version + "' pom.xml",
                "      xmlstarlet ed -L -u '_:project/_:properties/_:cedar.version' -v '" + version + "' pom.xml"]
            build_command = '      mvn clean install -DskipTests'
        elif repo.repo_type == RepoType.JAVA_WRAPPER or repo.repo_type == RepoType.JAVA:
            replace_version_commands = [
                '      mvn versions:set -DnewVersion="' + version + '" -DupdateMatchingVersions=false',
                '      mvn versions:update-parent versions:update-child-modules',
                '      mvn -DallowSnapshots=false versions:update-properties']
            build_command = '      mvn clean install -DskipTests'

        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
            'echo "Update to next release version"',
            *replace_version_commands,
            'echo "Build release version"',
            build_command,
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def prepare_angular_js(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angularJS project", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_angular_js_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_angular_js_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_angular_js_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
            *cls.macro_update_package_json_and_travis(version),
            *cls.macro_build_angular_js(),
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def prepare_angular_src(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular standalone project", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_angular_src_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_angular_src_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_angular_src_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
            *cls.macro_update_package_json_and_travis(version),
            *cls.macro_update_index_html_version_numbers(version),
            *cls.macro_build_angular(),
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def prepare_angular_dist(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular dist standalone project", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_angular_dist_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_angular_dist_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_angular_dist_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
            *cls.macro_update_package_json_and_travis(version),
            *cls.macro_build_angular(),
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def prepare_angular_src_sub(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular sub-project", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_angular_src_sub_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_angular_src_sub_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_angular_src_sub_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_update_package_json_and_travis(version),
            *cls.macro_update_index_html_version_numbers(version),
            *cls.macro_build_angular(),
        ])

    @classmethod
    def prepare_angular_dist_sub(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular dist sub-project", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_angular_dist_sub_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_angular_dist_sub_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_angular_dist_sub_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_update_package_json_and_travis(version),
        ])

    @classmethod
    def prepare_plain_sub(cls, repo):
        task = PlanTask("Prepare release of plain sub repo", TaskType.SHELL, repo)
        task.command_list = [
        ]
        return task

    @classmethod
    def prepare_multi_pre(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of multi directory project", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_multi_pre_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_multi_pre_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_multi_pre_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
        ])

    @classmethod
    def prepare_multi_post(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Wrap up release of multi directory project", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_multi_post_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_multi_post_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_multi_post_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def prepare_plain(cls, repo):
        task = PlanTask("Prepare release of plain repo", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_plain_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_plain_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_plain_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def prepare_development(cls, repo):
        task = PlanTask("Prepare release of development repo", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_development_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_development_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_development_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
            *cls.macro_update_development_cedar_version(version),
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def prepare_docker_deploy(cls, repo):
        task = PlanTask("Prepare release of Docker deploy repo", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_docker_deploy_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_docker_deploy_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_docker_deploy_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
            *cls.macro_update_env_cedar_docker_version(version),
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def prepare_docker_build(cls, repo):
        task = PlanTask("Prepare release of Docker build repo", TaskType.SHELL, repo)
        task.command_list = []
        release_version, release_pre_branch_name, release_tag_name, release_next_dev_version, release_post_branch_name = Util.get_release_vars()
        cls.prepare_docker_build_internal(repo, task, release_version, release_pre_branch_name, release_tag_name)
        cls.prepare_docker_build_internal(repo, task, release_next_dev_version, release_post_branch_name)
        return task

    @classmethod
    def prepare_docker_build_internal(cls, repo: Repo, task: PlanTask, version: str, branch_name: str, tag_name: str = None):
        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
            *cls.macro_update_docker_build_versions(version),
            *cls.macro_commit_changes(branch_name),
            *(cls.macro_tag_repo(tag_name) if tag_name is not None else [])
        ])

    @classmethod
    def macro_tag_repo(cls, tag_name: str):
        return ('echo "Tag repo with version"',
                '      git tag "' + tag_name + '"',
                '      git push origin "' + tag_name + '"')

    @classmethod
    def macro_create_pre_release_branch(cls, branch_name: str):
        return ('echo "Create branch"',
                '      git checkout develop',
                '      git pull origin develop',
                '      git checkout -b ' + branch_name)

    @classmethod
    def macro_commit_changes(cls, branch_name: str):
        return ('echo "Commit changes after build"',
                '      git add .',
                '      git commit -a -m "Produce version of component"',
                '      git push origin ' + branch_name)

    @classmethod
    def macro_update_index_html_version_numbers(cls, version: str):
        return ('echo "Update openview index.html"',
                "      sed -i '' 's/\/cedar-form-.*\.js/\/cedar-form-'" + version + "'\.js/g' src/index.html",
                "      sed -i '' 's/\/cedar-embeddable-editor-.*\.js/\/cedar-embeddable-editor-'" + version + "'\.js/g' src/index.html")

    @classmethod
    def macro_update_package_json_and_travis(cls, version: str):
        return ('echo "Update to next release version"',
                "      jq '.version=\"'" + version + "'\"' package.json | sponge package.json",
                "      sed -i '' 's/- CEDAR_VERSION\s*=.*\".*\"/- CEDAR_VERSION=\"'" + version + "'\"/g' .travis.yml")

    @classmethod
    def macro_update_development_cedar_version(cls, version: str):
        return ('echo "Update to next release version"',
                "      sed -i '' 's/^export CEDAR_VERSION=.*$/export CEDAR_VERSION='" + version + "'/' ./bin/util/set-env-generic.sh")

    @classmethod
    def macro_update_env_cedar_docker_version(cls, version: str):
        return ('echo "Update to next release version"',
                "      find . -name .env -exec sed -i '' 's/^CEDAR_DOCKER_VERSION=.*$/export CEDAR_DOCKER_VERSION='" + version + "'/' {} \; -print")

    @classmethod
    def macro_update_docker_build_versions(cls, version: str):
        return ('echo "Update to next release version"',
                "      find . -name Dockerfile -exec sed -i '' 's/^FROM metadatacenter\/cedar-microservice:.*$/FROM metadatacenter\/cedar-microservice:'" + version + "'/' {} \; -print",
                "      find . -name Dockerfile -exec sed -i '' 's/^FROM metadatacenter\/cedar-java:.*$/FROM metadatacenter\/cedar-java:'" + version + "'/' {} \; -print",
                "      find . -name Dockerfile -exec sed -i '' 's/^ENV CEDAR_VERSION=.*$/ENV CEDAR_VERSION='" + version + "'/' {} \; -print",
                "      sed -i '' 's/^export IMAGE_VERSION=.*$/export IMAGE_VERSION='" + version + "'/' ./bin/cedar-images-base.sh"
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

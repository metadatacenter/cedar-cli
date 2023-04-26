from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Util import Util


class ReleasePrepareShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def prepare_java_wrapper(cls, repo: Repo) -> PlanTask:
        from org.metadatacenter.util.GlobalContext import GlobalContext
        task = PlanTask("Prepare release of java wrapper", TaskType.SHELL, repo)

        release_version, release_branch_name, release_tag_name = Util.get_release_vars()

        replace_version_command_1 = ''
        replace_version_command_2 = ''
        replace_version_command_3 = ''
        build_command = ''
        if repo in GlobalContext.repos.get_parent():
            replace_version_command_1 = "xmlstarlet ed -L -u '_:project/_:version' -v '" + release_version + "' pom.xml"
            replace_version_command_2 = "xmlstarlet ed -L -u '_:project/_:properties/_:cedar.version' -v '" + release_version + "' pom.xml"
            build_command = 'mvn clean install -DskipTests'
        elif repo.repo_type == RepoType.JAVA_WRAPPER or repo.repo_type == RepoType.JAVA:
            replace_version_command_1 = 'mvn versions:set -DnewVersion="' + release_version + '" -DupdateMatchingVersions=false'
            replace_version_command_2 = 'mvn versions:update-parent versions:update-child-modules'
            replace_version_command_3 = 'mvn -DallowSnapshots=false versions:update-properties'
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

    @classmethod
    def prepare_angular_js(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angularJS project", TaskType.SHELL, repo)

        release_version, release_branch_name, release_tag_name = Util.get_release_vars()

        task.command_list = [
            'echo "Create pre release branch"',
            'git checkout develop',
            'git pull origin develop',
            'git checkout -b ' + release_branch_name,

            'echo "Update to next release version"',
            "jq '.version=\"'" + release_version + "'\"' package.json | sponge package.json",
            "sed -i '' 's/- CEDAR_VERSION\s*=.*\".*\"/- CEDAR_VERSION=\"'" + release_version + "'\"/g' .travis.yml",

            'echo "Build release version"',
            'npm install',

            'echo "Commit changes after build"',
            'git add .',
            'git commit -a -m "Produce release version of component"',
            'git push origin ' + release_branch_name,

            'echo "Tag repo with release version"',
            'git tag "' + release_tag_name + '"',
            'git push origin "' + release_tag_name + '"'
        ]
        return task

    @classmethod
    def prepare_angular_src_sub(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular sub-project", TaskType.SHELL, repo)

        release_version, release_branch_name, release_tag_name = Util.get_release_vars()

        task.command_list = [
            'echo "Update to next release version"',
            "jq '.version=\"'" + release_version + "'\"' package.json | sponge package.json",
            "sed -i '' 's/- CEDAR_VERSION\s*=.*\".*\"/- CEDAR_VERSION=\"'" + release_version + "'\"/g' .travis.yml",

            'echo "Update openview index.html"',
            "sed -i '' 's/\/cedar-form-.*\.js/\/cedar-form-'" + release_version + "'\.js/g' src/index.html",
            "sed -i '' 's/\/component\.metadatacenter\..*\/cedar-form\//\/component\.metadatacenter\.org\/cedar-form\//g' src/index.html",
            "sed -i '' 's/\/cedar-embeddable-editor-.*\.js/\/cedar-embeddable-editor-'" + release_version + "'\.js/g' src/index.html",
            "sed -i '' 's/\/component\.metadatacenter\..*\/cedar-embeddable-editor\//\/component\.metadatacenter\.org\/cedar-embeddable-editor\//g' src/index.html",

            'echo "Build release version"',
            'npm install --legacy-peer-deps',
            'ng build --configuration=production',
        ]
        return task

    @classmethod
    def prepare_angular_dist_sub(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of angular dist sub-project", TaskType.SHELL, repo)

        release_version, release_branch_name, release_tag_name = Util.get_release_vars()

        task.command_list = [
            'echo "Update to next release version"',
            "jq '.version=\"'" + release_version + "'\"' package.json | sponge package.json",
        ]
        return task

    @classmethod
    def prepare_multi_pre(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Prepare release of multi directory project", TaskType.SHELL, repo)

        release_version, release_branch_name, release_tag_name = Util.get_release_vars()

        task.command_list = [
            'echo "Create pre release branch"',
            'git checkout develop',
            'git pull origin develop',
            'git checkout -b ' + release_branch_name,
        ]
        return task

    @classmethod
    def prepare_multi_post(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Wrap up release of multi directory project", TaskType.SHELL, repo)

        release_version, release_branch_name, release_tag_name = Util.get_release_vars()

        task.command_list = [
            'echo "Commit changes after build"',
            'git add .',
            'git commit -a -m "Produce release version of component"',
            'git push origin ' + release_branch_name,

            'echo "Tag repo with release version"',
            'git tag "' + release_tag_name + '"',
            'git push origin "' + release_tag_name + '"'
        ]
        return task

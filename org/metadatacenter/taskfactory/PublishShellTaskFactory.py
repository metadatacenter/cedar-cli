from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType

# npm 11 refuses to publish a prerelease version unless a --tag is given on the command line, and
# publishing a prerelease to `latest` was never what we wanted anyway. npm 10 allowed it, which is
# why a bare `npm publish` worked for years and then broke the moment a build host was reprovisioned
# with a current Node.
#
# The tag cannot be decided when the plan is built: the same command list serves the develop-snapshot
# pass (X.Y.Z-SNAPSHOT, a prerelease) and the release pass (X.Y.Z), and the working tree is on a
# different branch for each. So the version is read from package.json at execution time instead.
#
# `publishConfig.tag` is not an alternative -- npm 11 ignores it. The release train publishes its own
# packed tarballs with an explicit --tag and does not come through here.
NPM_PUBLISH = (
    "npm publish "
    "$(node -p \"require('./package.json').version.includes('-') ? '--tag=dev' : ''\")"
)


class PublishShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def maven_deploy_skip_tests(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Maven deploy skip tests", TaskType.SHELL, repo)
        task.command_list = ['./mvnw deploy -DskipTests']
        return task

    @classmethod
    def npm_publish(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM publish", TaskType.SHELL, repo)
        task.command_list = [NPM_PUBLISH]
        return task

    @classmethod
    def npm_install_publish(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM install, NPM publish", TaskType.SHELL, repo)
        task.command_list = ['npm install', NPM_PUBLISH]
        return task

    @classmethod
    def repo_publish_commands(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Repo-specific publish", TaskType.SHELL, repo)
        task.command_list = list(repo.publish_command_list)
        return task

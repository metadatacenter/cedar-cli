from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType


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
        task.command_list = ['npm publish']
        return task

    @classmethod
    def npm_install_publish(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM install, NPM publish", TaskType.SHELL, repo)
        task.command_list = ['npm install', 'npm publish']
        return task

    @classmethod
    def repo_publish_commands(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Repo-specific publish", TaskType.SHELL, repo)
        task.command_list = list(repo.publish_command_list)
        return task

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType


class DeployShellTaskFactory:

    def __init__(self):
        super().__init__()

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

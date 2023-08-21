from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.RepoRelationType import RepoRelationType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.BuildOperator import BuildOperator
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.BuildShellTaskFactory import BuildShellTaskFactory
from org.metadatacenter.taskfactory.DeployShellTaskFactory import DeployShellTaskFactory
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

console = Console()


class DeployOperator(Operator):

    def __init__(self):
        super().__init__()

    @staticmethod
    def expand(task: PlanTask):
        repo_list = [task.repo]
        repo_list_flat = Util.get_flat_repo_list(repo_list)
        for repo in repo_list_flat:
            if repo.repo_type == RepoType.JAVA_WRAPPER:
                shell_wrapper = PlanTask("Deploy java wrapper project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(DeployShellTaskFactory.maven_deploy_skip_tests(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.JAVA:
                shell_wrapper = PlanTask("Deploy java project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(DeployShellTaskFactory.maven_deploy_skip_tests(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR:
                shell_wrapper = PlanTask("Deploy angular project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(BuildShellTaskFactory.npm_install_legacy_ng_build(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR_DIST:
                shell_wrapper = PlanTask("Deploy angular dist project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(DeployShellTaskFactory.npm_publish(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR_JS:
                shell_wrapper = PlanTask("Deploy angularJS project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(DeployShellTaskFactory.npm_install_publish(repo))
                task.add_task_as_task(shell_wrapper)
            else:
                not_handled = PlanTask("Skip repo", TaskType.NOOP, repo)
                not_handled.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(not_handled)

            source_of_relations = GlobalContext.repos.get_relations(repo, RepoRelationType.IS_SOURCE_OF)
            for source_of_relation in source_of_relations:
                BuildOperator.handle_is_source_of(source_of_relation, task)

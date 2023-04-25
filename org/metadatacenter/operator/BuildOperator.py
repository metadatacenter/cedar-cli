from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.ShellTaskFactory import ShellTaskFactory
from org.metadatacenter.util.Util import Util

console = Console()


class BuildOperator(Operator):

    def __init__(self):
        super().__init__()

    def expand(self, task: PlanTask):
        repo_list = [task.repo]
        repo_list_flat = Util.get_flat_repo_list(repo_list)
        for repo in repo_list_flat:
            if repo.repo_type == RepoType.JAVA_WRAPPER:
                shell_wrapper = PlanTask("Build java wrapper project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ShellTaskFactory.maven_clean_install_skip_tests(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.JAVA:
                shell_wrapper = PlanTask("Build java project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ShellTaskFactory.maven_clean_install_skip_tests(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR:
                shell_wrapper = PlanTask("Build angular project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ShellTaskFactory.npm_install_legacy_ng_build(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR_JS:
                shell_wrapper = PlanTask("Build angularJS project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ShellTaskFactory.npm_install(repo))
                task.add_task_as_task(shell_wrapper)
            else:
                not_handled = PlanTask("Skip repo", TaskType.SKIP, repo)
                task.add_task_as_task(not_handled)

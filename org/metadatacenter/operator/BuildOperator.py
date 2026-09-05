import os

from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.BuildShellTaskFactory import BuildShellTaskFactory
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

console = Console()


class BuildOperator(Operator):

    def __init__(self):
        super().__init__()

    @staticmethod
    def expand(task: PlanTask):
        repo_list = [task.repo]
        repo_list_flat = Util.get_flat_repo_list(repo_list)
        build_frontends = (task.get_parameter("force_frontend_build") is True or
                           (Const.CEDAR_DEV_BUILD_FRONTENDS in os.environ and
                            os.environ[Const.CEDAR_DEV_BUILD_FRONTENDS] == 'true'))
        java_build = (BuildShellTaskFactory.maven_clean_install_skip_tests
                      if GlobalContext.should_skip_tests()
                      else BuildShellTaskFactory.maven_clean_install)
        for repo in repo_list_flat:
            if repo.repo_type == RepoType.JAVA_WRAPPER:
                shell_wrapper = PlanTask("Build java wrapper project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(java_build(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.JAVA:
                shell_wrapper = PlanTask("Build java project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(java_build(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR:
                if build_frontends:
                    shell_wrapper = PlanTask("Build angular project", TaskType.SHELL_WRAPPER, repo)
                    if repo.build_command_list:
                        build_task = BuildShellTaskFactory.repo_build_commands(repo)
                    else:
                        build_task = BuildShellTaskFactory.npm_ci_legacy_ng_build(repo)
                    build_task.parameters["isolated_frontend_build"] = True
                    shell_wrapper.add_task_as_task(build_task)
                else:
                    shell_wrapper = PlanTask("Build angular project - skipped because of CEDAR_DEV_BUILD_FRONTENDS", TaskType.SHELL_WRAPPER,
                                             repo)
                    shell_wrapper.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR_DIST:
                shell_wrapper = PlanTask("Build angular dist project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR_JS:
                if build_frontends:
                    shell_wrapper = PlanTask("Build angularJS project", TaskType.SHELL_WRAPPER, repo)
                    if task.get_parameter("server_frontend_payload") is True and repo.server_build_command_list:
                        build_task = BuildShellTaskFactory.repo_server_build_commands(repo)
                        build_task.parameters["in_place_frontend_build"] = True
                    elif repo.build_command_list:
                        build_task = BuildShellTaskFactory.repo_build_commands(repo)
                    else:
                        build_task = BuildShellTaskFactory.npm_ci(repo)
                    if task.get_parameter("server_frontend_payload") is not True:
                        build_task.parameters["isolated_frontend_build"] = True
                    shell_wrapper.add_task_as_task(build_task)
                else:
                    shell_wrapper = PlanTask("Build angularJS project - skipped because of CEDAR_DEV_BUILD_FRONTENDS",
                                             TaskType.SHELL_WRAPPER, repo)
                    shell_wrapper.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.TYPESCRIPT:
                shell_wrapper = PlanTask("Build TypeScript project", TaskType.SHELL_WRAPPER, repo)
                commands = []
                if not repo.skip_npm_install:
                    commands.extend(BuildShellTaskFactory.npm_ci(repo).command_list)
                commands.extend(BuildShellTaskFactory.npm_run_build(repo).command_list)
                build_task = PlanTask("Isolated TypeScript build", TaskType.SHELL, repo)
                build_task.command_list = commands
                build_task.parameters["isolated_frontend_build"] = True
                build_task.parameters["reuse_node_modules"] = repo.skip_npm_install
                shell_wrapper.add_task_as_task(build_task)
                task.add_task_as_task(shell_wrapper)
            else:
                not_handled = PlanTask("Skip repo", TaskType.NOOP, repo)
                not_handled.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(not_handled)

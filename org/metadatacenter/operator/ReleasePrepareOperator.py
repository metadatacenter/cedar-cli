from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.PrePostType import PrePostType
from org.metadatacenter.model.RepoRelationType import RepoRelationType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.operator.BuildOperator import BuildOperator
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.BuildShellTaskFactory import BuildShellTaskFactory
from org.metadatacenter.taskfactory.ReleasePrepareShellTaskFactory import ReleasePrepareShellTaskFactory
from org.metadatacenter.util.GlobalContext import GlobalContext

console = Console()


class ReleasePrepareOperator(Operator):

    def __init__(self):
        super().__init__()

    @staticmethod
    def expand(task: PlanTask):
        repo_list = [task.repo]
        # repo_list_flat = Util.get_flat_repo_list(repo_list)
        for repo in repo_list:
            if repo.repo_type == RepoType.JAVA_WRAPPER:
                shell_wrapper = PlanTask("Prepare release of java wrapper project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_java(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.JAVA:
                shell_wrapper = PlanTask("Prepare release of java project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_java(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR:
                if repo.pre_post_type == PrePostType.SUB:
                    shell_wrapper = PlanTask("Prepare release of angular sub-project", TaskType.SHELL_WRAPPER, repo)
                    shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_angular_src_sub(repo))
                    task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR_DIST:
                if repo.pre_post_type == PrePostType.SUB:
                    shell_wrapper = PlanTask("Prepare release of angular dist sub-project", TaskType.SHELL_WRAPPER, repo)
                    shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_angular_dist_sub(repo))
                    task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR_JS:
                shell_wrapper = PlanTask("Prepare release of angularJS project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_angular_js(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.MULTI:
                if repo.pre_post_type == PrePostType.PRE:
                    shell_wrapper = PlanTask("Prepare release multi project", TaskType.SHELL_WRAPPER, repo)
                    shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_multi_pre(repo))
                    task.add_task_as_task(shell_wrapper)
                elif repo.pre_post_type == PrePostType.POST:
                    shell_wrapper = PlanTask("Wrap up release multi project", TaskType.SHELL_WRAPPER, repo)
                    shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_multi_post(repo))
                    task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.MKDOCS:
                shell_wrapper = PlanTask("Prepare release of mkdocs repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_plain(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.CONTENT_DELIVERY:
                shell_wrapper = PlanTask("Prepare release of content delivery repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_plain(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.MISC:
                shell_wrapper = PlanTask("Prepare release of miscellaneous repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_plain(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.PYTHON:
                shell_wrapper = PlanTask("Prepare release of python repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_plain(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.PHP:
                shell_wrapper = PlanTask("Prepare release of PHP repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_plain(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.DEVELOPMENT:
                shell_wrapper = PlanTask("Prepare release of development repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_development(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.DOCKER_DEPLOY:
                shell_wrapper = PlanTask("Prepare release of Docker deploy repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_docker_deploy(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.DOCKER_BUILD:
                shell_wrapper = PlanTask("Prepare release of Docker build repo", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(ReleasePrepareShellTaskFactory.prepare_docker_build(repo))
                task.add_task_as_task(shell_wrapper)
            else:
                not_handled = PlanTask("Skip repo", TaskType.NOOP, repo)
                not_handled.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(not_handled)

            source_of_relation = GlobalContext.repos.get_relation(repo, RepoRelationType.IS_SOURCE_OF)
            if source_of_relation is not None:
                BuildOperator.handle_is_source_of(source_of_relation, task)

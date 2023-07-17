import os

from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.RepoRelation import RepoRelation
from org.metadatacenter.model.RepoRelationType import RepoRelationType
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
        build_frontends = Const.CEDAR_DEV_BUILD_FRONTENDS in os.environ and os.environ[Const.CEDAR_DEV_BUILD_FRONTENDS] == 'true'
        for repo in repo_list_flat:
            if repo.repo_type == RepoType.JAVA_WRAPPER:
                shell_wrapper = PlanTask("Build java wrapper project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(BuildShellTaskFactory.maven_clean_install_skip_tests(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.JAVA:
                shell_wrapper = PlanTask("Build java project", TaskType.SHELL_WRAPPER, repo)
                shell_wrapper.add_task_as_task(BuildShellTaskFactory.maven_clean_install_skip_tests(repo))
                task.add_task_as_task(shell_wrapper)
            elif repo.repo_type == RepoType.ANGULAR:
                if build_frontends:
                    shell_wrapper = PlanTask("Build angular project", TaskType.SHELL_WRAPPER, repo)
                    shell_wrapper.add_task_as_task(BuildShellTaskFactory.npm_install_legacy_ng_build(repo))
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
                    shell_wrapper.add_task_as_task(BuildShellTaskFactory.npm_install(repo))
                else:
                    shell_wrapper = PlanTask("Build angularJS project - skipped because of CEDAR_DEV_BUILD_FRONTENDS",
                                             TaskType.SHELL_WRAPPER, repo)
                    shell_wrapper.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(shell_wrapper)
            else:
                not_handled = PlanTask("Skip repo", TaskType.NOOP, repo)
                not_handled.add_task_as_task(BuildShellTaskFactory.noop(repo))
                task.add_task_as_task(not_handled)

            source_of_relation = GlobalContext.repos.get_relation(repo, RepoRelationType.IS_SOURCE_OF)
            if source_of_relation is not None and build_frontends:
                BuildOperator.handle_is_source_of(source_of_relation, task)

    @staticmethod
    def handle_is_source_of(source_of_relation: RepoRelation, task: PlanTask):
        source_repo = source_of_relation.source_repo
        target_repo = source_of_relation.target_repo
        if source_repo.repo_type == RepoType.ANGULAR:

            action = "copy"
            params = source_of_relation.parameters
            relative_source_path = 'dist/' + source_repo.name
            if RepoRelation.SOURCE_SUB_FOLDER in params:
                relative_source_path = params[RepoRelation.SOURCE_SUB_FOLDER]

            relative_target_path = ''
            if RepoRelation.TARGET_SUB_FOLDER in params:
                relative_target_path = params[RepoRelation.TARGET_SUB_FOLDER]

            source_selector = '*'
            if RepoRelation.SOURCE_SELECTOR in params:
                source_selector = params[RepoRelation.SOURCE_SELECTOR]

            target_selector = '.'
            if RepoRelation.DESTINATION_CONCAT in params:
                target_selector = params[RepoRelation.DESTINATION_CONCAT]
                target_selector = target_selector.replace('${CEDAR_VERSION}', Util.get_build_version(task))
                action = "concat"

            source_path = os.path.join(Util.get_wd(source_repo), relative_source_path, source_selector)
            target_path = os.path.join(Util.get_wd(target_repo), relative_target_path, target_selector)

            if action == "copy":
                shell_wrapper = PlanTask("Copy compiled angular code to dist", TaskType.SHELL_WRAPPER, source_repo)
                task.add_task_as_task(BuildShellTaskFactory.copy_src_content_to_dest(source_path, target_path, source_repo))
            else:
                shell_wrapper = PlanTask("Cat compiled angular code to dist", TaskType.SHELL_WRAPPER, source_repo)
                task.add_task_as_task(BuildShellTaskFactory.cat_src_content_to_dest(source_path, target_path, source_repo))
            task.add_task_as_task(shell_wrapper)

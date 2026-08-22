from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType


class BuildShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def maven_clean_install(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Maven clean install", TaskType.SHELL, repo)
        task.command_list = ['./mvnw clean install']
        return task

    @classmethod
    def maven_clean_install_skip_tests(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Maven clean install skip tests", TaskType.SHELL, repo)
        task.command_list = ['./mvnw clean install -DskipTests']
        return task

    @classmethod
    def npm_install_legacy_ng_build(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM install, NG build", TaskType.SHELL, repo)
        task.command_list = ['npm install --legacy-peer-deps', 'ng build --configuration=production']
        return task

    @classmethod
    def repo_build_commands(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Repo-specific build", TaskType.SHELL, repo)
        task.command_list = list(repo.build_command_list)
        return task

    @classmethod
    def repo_server_build_commands(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Repo-specific server payload build", TaskType.SHELL, repo)
        task.command_list = list(repo.server_build_command_list)
        return task

    @classmethod
    def npm_install(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM install", TaskType.SHELL, repo)
        task.command_list = ['npm install']
        return task

    @classmethod
    def npm_run_build(cls, repo: Repo) -> PlanTask:
        task = PlanTask("NPM run build", TaskType.SHELL, repo)
        task.command_list = ['npm run build']
        return task

    @classmethod
    def copy_src_content_to_dest(cls, source_path: str, target_path: str, repo: Repo) -> PlanTask:
        task = PlanTask("Copy source directory content into destination directory", TaskType.SHELL, repo)
        command = "cp -a " + source_path + " " + target_path
        task.command_list = [command]
        return task

    @classmethod
    def cat_src_content_to_dest(cls, source_path: str, target_path: str, repo: Repo) -> PlanTask:
        task = PlanTask("Cat source directory content into destination directory", TaskType.SHELL, repo)
        command = "cat " + source_path + " > " + target_path
        task.command_list = [command]
        return task

    @classmethod
    def noop(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Nothing to do", TaskType.SHELL, repo)
        task.command_list = ['echo "Nothing to do"']
        return task

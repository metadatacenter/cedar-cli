from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.Task import Task
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.Worker import Worker

console = Console()


class ReleasePrepareWorker(Worker):
    def __init__(self):
        super().__init__()

    def work(self, task: Task, parent_task_repo=None):
        repo_list = task.repo_list
        title = task.title
        progress_text = task.progress_text
        msg = "[cyan]" + title
        repo_list_flat = self.get_flat_repo_list(repo_list)
        for repo in repo_list_flat:
            msg += "\n  " + "️ ➡️  " + repo.get_wd()
        console.print(Panel(msg, style=Style(color="cyan"), title="Deploy worker"))
        release_branch_name = "pre-release-2023-04-24-12-34-01"
        for repo in repo_list_flat:
            handled = False
            # if repo.repo_type == RepoType.JAVA_WRAPPER:
            #     self.execute_shell_command_list(repo, ["mvn deploy -DskipTests"], progress_text)
            #     handled = True
            # elif repo.repo_type == RepoType.JAVA:
            #     self.execute_shell_command_list(repo, ["mvn deploy -DskipTests"], progress_text)
            #     handled = True
            # elif repo.repo_type == RepoType.ANGULAR:
            #     self.execute_shell_command_list(repo, ["npm install --legacy-peer-deps; ng build --configuration=production; npm publish"],
            #                                     progress_text)
            #     handled = True
            # elif repo.repo_type == RepoType.ANGULAR_JS:
            #     self.execute_shell_command_list(repo, ["npm install; npm publish"], progress_text)
            #     handled = True

            if repo.repo_type == RepoType.ANGULAR:
                self.execute_shell_command_list(repo, [
                    'git checkout develop',
                    'git pull origin develop',
                    'git checkout -b ' + release_branch_name,
                    # '=> MACRO replace version to ${CEDAR_RELEASE_VERSION}',
                    '=> MACRO replace version',
                    '=> MACRO BUILD, with all sub-steps, and postActions!!',
                    # Macro_function_call_here,
                    'git add .',
                    'git commit -a -m "Produce release version of component"',
                    'git push origin ' + release_branch_name,
                    '=> MACRO tag_repo_with_release_version',
                    '=> MACRO copy_release_to_main',
                    '=> MACRO publish using all sub-steps',
                    'git checkout develop',
                    # '=> MACRO replace version to ${CEDAR_NEXT_DEVELOPMENT_VERSION}',
                    '=> MACRO replace version to ',
                    '=> MACRO BUILD, with all sub-steps, and postActions!!',
                    'git add .',
                    'git commit -a -m "Updated to next development version"',
                    'git push origin develop',
                    '=> publish using all sub-steps'
                ], progress_text)
                handled = True

            if not handled:
                self.execute_none(repo, progress_text)
            # GlobalContext().trigger_post_task(repo, task)

import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.rule import Rule
from rich.style import Style
from rich.table import Table

from org.metadatacenter import Repos, Repo
from org.metadatacenter.ResultTable import ResultTable
from org.metadatacenter.Worker import Worker

console = Console()

git_base = "https://github.com/metadatacenter/"


class GitWorker(Worker):
    def __init__(self, repos: Repos):
        super().__init__(repos)

    def get_wd(self, repo: Repo):
        return self.cedar_home + "/" + repo.name

    def execute_shell_with_table(self,
                                 command_list,
                                 cwd_is_home=False,
                                 headers=["Repo", "Output", "Error"],
                                 show_lines=True,
                                 status_line="Processing",
                                 repo_list=None
                                 ):
        result = ResultTable(headers, show_lines)
        if repo_list is None:
            repo_list = self.repos.get_list()
        with Progress() as progress:
            task = progress.add_task("[red]" + status_line + "...", total=len(repo_list))
            for repo in repo_list:
                commands_to_execute = [cmd.format(repo.name) for cmd in command_list]
                rule = Rule("[bold red]" + repo.name)
                progress.print(rule)
                out = ""
                err = ""
                try:
                    cwd = self.get_wd(repo) if cwd_is_home is False else self.cedar_home
                    # print(commands_to_execute)
                    process = subprocess.Popen(commands_to_execute, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, cwd=cwd)
                    stdout, stderr = process.communicate()
                    out = stdout.decode('utf-8').strip()
                    err = stderr.decode('utf-8').strip()
                except subprocess.CalledProcessError as e:
                    err += str(e)
                except OSError as e:
                    err += str(e)
                except:
                    err += "Error in subprocess"
                result.add_line(repo.name, out, err)
                progress.print(out)
                if len(err) > 0:
                    progress.print(err)
                    progress.print(Panel(err, title="[bold yellow]Error", subtitle="[bold yellow]" + repo.name, style=Style(color="red")))
                progress.update(task, advance=1)
        result.print_table()
        return result

    def render_status_table(self, result):
        table = Table("Repo", "Output", "Error", "Suggested", show_lines=True)
        for repo in result.lines:
            if ("our branch is behind" in repo[1]):
                table.add_row(repo[0], repo[1][0:300] + '...', repo[2], "Pull")
            elif ("ntracked files" in repo[1]):
                table.add_row(repo[0], repo[1][0:300] + '...', repo[2], "Add and Push")
            elif ("hanges not staged" in repo[1]):
                table.add_row(repo[0], repo[1][0:300] + '...', repo[2], "Add and Push")
        console.print(table)

    def list_repos(self):
        table = Table("Repo", "Type", "distSrc", "isLibrary", "isClient", "isMicroservice", "isPrivate", "forDocker")
        for repo in self.repos.get_list():
            is_library = "✅" if repo.is_library else ""
            is_client = "✅" if repo.is_client else ""
            is_microservice = "✅" if repo.is_microservice else ""
            is_private = "✅" if repo.is_private else ""
            for_docker = "✅" if repo.for_docker else ""
            table.add_row(repo.name, repo.repo_type, repo.dist_src, is_library, is_client, is_microservice, is_private, for_docker)
        console.print(table)

    def branch(self):
        self.execute_shell_with_table(
            command_list=["echo $(git rev-parse --abbrev-ref HEAD)"],
            headers=["Repo", "Branch", "Error"],
            show_lines=False,
            status_line="Checking",
        )

    def pull(self):
        self.execute_shell_with_table(
            command_list=["git pull"],
            status_line="Pulling",
        )

    def status(self):
        result = self.execute_shell_with_table(
            command_list=["git status"],
        )
        self.render_status_table(result)

    def checkout(self, branch: str):
        self.execute_shell_with_table(
            command_list=["git checkout " + branch],
            status_line="Checking out",
        )

    def clone_docker(self):
        self.execute_shell_with_table(
            status_line="Cloning",
            repo_list=self.repos.get_for_docker_list(),
            command_list=["git clone " + git_base + "{0}"],
            cwd_is_home=True,
        )

    def clone_all(self):
        self.execute_shell_with_table(
            status_line="Cloning",
            command_list=["git clone " + git_base + "{0}"],
            cwd_is_home=True,
        )

import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.rule import Rule
from rich.style import Style
from rich.table import Table

from org.metadatacenter.model import Repo, Repos
from org.metadatacenter.util.RepoResultTriple import RepoResultTriple
from org.metadatacenter.util.ResultTable import ResultTable
from org.metadatacenter.worker.Worker import Worker

console = Console()

git_base = "https://github.com/metadatacenter/"
NEXT_GIT_FILE = 'next_git_repo'
LAST_GIT_FILE = 'last_git_repo'
UTF_8 = 'utf-8'
GIT_STATUS_CHAR_LIMIT = 300


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
            repo_list = self.repos.get_list_top()
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
                    out = stdout.decode(UTF_8).strip()
                    err = stderr.decode(UTF_8).strip()
                except subprocess.CalledProcessError as e:
                    err += str(e)
                except OSError as e:
                    err += str(e)
                except:
                    err += "Error in subprocess"
                result.add_result(RepoResultTriple(repo, out, err))
                progress.print(out)
                if len(err) > 0:
                    progress.print(err)
                    progress.print(Panel(err, title="[bold yellow]Error", subtitle="[bold yellow]" + repo.name, style=Style(color="red")))
                progress.update(task, advance=1)
        result.print_table()
        return result

    def register_active_repo(self, triple, table, active_repos, suggestion):
        table.add_row(triple.repo.name, triple.out[0:GIT_STATUS_CHAR_LIMIT] + '...', triple.err, suggestion)
        active_repos.append(triple.repo)

    def render_status_table(self, result):
        active_repos = []
        table = Table("Repo", "Output", "Error", "Suggested", show_lines=True, title="Repos that require attention")
        cnt = 0
        for triple in result.results:
            if "our branch is behind" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Pull")
                cnt += 1
            elif "ntracked files" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Add, Commit, Push")
                cnt += 1
            elif "hanges not staged" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Add, Commit, Push")
                cnt += 1
            elif "hanges to be committed" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Commit, Push")
                cnt += 1
            elif "our branch is ahead of" in triple.out:
                self.register_active_repo(triple, table, active_repos, "Push")
                cnt += 1
        if cnt > 0:
            table.caption = str(cnt) + " repos to act on"
            table.style = Style(color="red")
            console.print()
            console.print(table)
        else:
            console.print(Panel("Nothing to add, commit or push, all changes are published", style=Style(color="green")))
        return active_repos

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
        return self.render_status_table(result)

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

    def next(self):
        active_repos = self.status()
        if len(active_repos) > 0:
            last_repo_path = self.read_cedar_file('last_git_repo')
            found_idx = -1

            if last_repo_path is not None:
                for idx, repo in enumerate(active_repos):
                    if self.get_wd(repo) == last_repo_path:
                        found_idx = idx

            found_idx += 1
            if found_idx >= len(active_repos):
                found_idx = 0
            next_repo = active_repos[found_idx]
            path = self.get_wd(next_repo)
            console.print("Found repo with activity, changing current working directory to: " + path)
            self.write_cedar_file(LAST_GIT_FILE, path + "\n")
            self.write_cedar_file(NEXT_GIT_FILE, path + "\n")
        else:
            self.delete_cedar_file(LAST_GIT_FILE)
            self.delete_cedar_file(NEXT_GIT_FILE)

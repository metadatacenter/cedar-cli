import fcntl
import os
import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, TimeElapsedColumn, SpinnerColumn
from rich.style import Style

from org.metadatacenter.model import Repo, Repos

console = Console()


class Worker:
    def __init__(self, repos: Repos):
        self.repos = repos
        self.cedar_home = os.environ['CEDAR_HOME']

    def get_wd(self, repo: Repo):
        return self.cedar_home + "/" + repo.get_wd()

    @staticmethod
    def get_flat_repo_list(repo_list):
        repos = []
        for repo in repo_list:
            repos.append(repo)
            if len(repo.sub_repos) > 0:
                for sub_repo in repo.sub_repos:
                    repos.append(sub_repo)
        return repos

    @staticmethod
    def handle_shell_stdout(proc_stream, my_buffer, progress, task, echo_streams=True):
        try:
            for s in iter(proc_stream.readline, b''):
                out = s.decode('utf-8').strip()
                if len(out) > 0:
                    my_buffer.append(out)
                    if echo_streams:
                        progress.print(out, markup=False)
                    progress.update(task, advance=1)
        except IOError:
            pass

    def execute_shell(self,
                      repo,
                      command_list,
                      status,
                      cwd_is_home=False,
                      ):
        commands_to_execute = [cmd.format(repo.name) for cmd in command_list]
        cwd = self.get_wd(repo) if cwd_is_home is False else self.cedar_home
        console.print(Panel("[yellow]" + status + "\n" +
                            "Location : " + cwd + "\n" +
                            "Repo type: " + repo.repo_type + "\n" +
                            "Commands : " + "\n".join(commands_to_execute)),
                      style=Style(color="green"))
        proc = subprocess.Popen(commands_to_execute, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, cwd=cwd)

        proc_stdout = proc.stdout
        fl = fcntl.fcntl(proc_stdout, fcntl.F_GETFL)
        fcntl.fcntl(proc_stdout, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        stdout_parts = []
        with Progress(TextColumn("[progress.description]{task.description}"),
                      BarColumn(),
                      TaskProgressColumn(),
                      TimeRemainingColumn(),
                      SpinnerColumn(),
                      TimeElapsedColumn()) as progress:
            task = progress.add_task("[red]" + status + "...", total=repo.expected_build_lines)
            while proc.poll() is None:
                self.handle_shell_stdout(proc_stdout, stdout_parts, progress, task)

            self.handle_shell_stdout(proc_stdout, stdout_parts, progress, task)

        msg = "[green]" + status + " " + repo.name + ' done. '
        if len(stdout_parts) != repo.expected_build_lines:
            msg += "[yellow]" + str(len(stdout_parts)) + ' lines vs expected ' + str(repo.expected_build_lines)
        console.print(Panel(msg, style=Style(color="green")))
        return stdout_parts

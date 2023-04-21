import fcntl
import os
import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, TimeElapsedColumn, SpinnerColumn
from rich.style import Style

from org.metadatacenter.model.WorkerType import WorkerType
from org.metadatacenter.util.Util import Util

console = Console()


class Worker:
    worker_type: WorkerType

    @staticmethod
    def get_flat_repo_list(repo_list):
        repos = []
        for repo in repo_list:
            repos.append(repo)
            if len(repo.sub_repos) > 0:
                for sub_repo in repo.sub_repos:
                    repos.append(sub_repo)
        return repos

    def write_cedar_file(self, file_name, content):
        with open(self.get_cedar_file(file_name), "w") as file:
            file.write(content)

    def read_cedar_file(self, file_name):
        path = self.get_cedar_file(file_name)
        if not os.path.exists(path):
            return None
        with open(path, 'r') as file:
            return file.read().rstrip()

    def delete_cedar_file(self, file_name):
        path = self.get_cedar_file(file_name)
        if os.path.exists(path):
            os.remove(path)

    def get_cedar_file(self, file_name):
        parent_path = os.path.expanduser('~/.cedar/')
        if not os.path.exists(parent_path):
            os.makedirs(parent_path)
        return os.path.join(parent_path, file_name)

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

    def execute_shell_command_list(self,
                                   repo,
                                   command_list,
                                   status,
                                   cwd_is_home=False,
                                   ):
        commands_to_execute = [cmd.format(repo.name) for cmd in command_list]
        cwd = Util.get_wd(repo) if cwd_is_home is False else Util.cedar_home
        console.print(Panel("[green]" +
                            " 📂️ Location  : " + cwd + "\n" +
                            " 🏷️️  Repo type : " + repo.repo_type + "\n" +
                            " 🖥️  Commands  : " + "\n".join(commands_to_execute),
                            title="Execute shell command list",
                            title_align="left"),
                      style=Style(color="green"))
        for command in commands_to_execute:
            self.execute_shell_command(repo, command, status, cwd)

    def execute_none(self, repo, status):
        cwd = Util.get_wd(repo)
        console.print(Panel("[green]" +
                            " ➡️️  Repo      : " + repo.get_wd() + "\n" +
                            " 📂️ Location  : " + cwd + "\n" +
                            " 🏷️️  Repo type : " + repo.repo_type,
                            title="Execute none",
                            title_align="left"),
                      style=Style(color="green"))

    def execute_shell_command(self, repo, command, status, cwd):
        console.print(Panel("[yellow]" +
                            " 📂️ Location  : " + cwd + "\n" +
                            " 🏷️️  Repo type : " + repo.repo_type + "\n" +
                            " 🖥️  Command   : " + command,
                            title="Shell subprocess",
                            title_align="left"),
                      style=Style(color="yellow"))
        proc = subprocess.Popen([command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, cwd=cwd)

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
        console.print(Panel(msg, style=Style(color="green"), subtitle="Shell subprocess"))
        return stdout_parts

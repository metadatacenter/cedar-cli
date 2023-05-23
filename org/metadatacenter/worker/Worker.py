import os
import subprocess
from typing import List

import fcntl
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.rule import Rule
from rich.style import Style

from org.metadatacenter.model.WorkerType import WorkerType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.RepoResultTriple import RepoResultTriple
from org.metadatacenter.util.ResultTable import ResultTable
from org.metadatacenter.util.Util import Util

console = Console()

UTF_8 = 'utf-8'


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

    @staticmethod
    def execute_shell_on_all_repos_with_table(command_list,
                                              cwd_is_home=False,
                                              headers=None,
                                              show_lines=True,
                                              status_line="Processing",
                                              repo_list=None
                                              ):
        if headers is None:
            headers = ["Repo", "Output", "Error"]
        result = ResultTable(headers, show_lines)
        if repo_list is None:
            repo_list = GlobalContext.repos.get_list_top()
        with Progress() as progress:
            task = progress.add_task("[red]" + status_line + "...", total=len(repo_list))
            for repo in repo_list:
                commands_to_execute = [cmd.format(repo.name) for cmd in command_list]
                rule = Rule("[bold red]" + repo.name)
                progress.print(rule)
                out = ""
                err = ""
                try:
                    cwd = Util.get_wd(repo) if cwd_is_home is False else Util.cedar_home
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

    @staticmethod
    def execute_generic_shell_commands(command_list: List[str], title: str, cwd: str = None):
        panel = Panel(
            "[yellow]" +
            ((" 📂️ Location  : " + cwd + "\n") if cwd else '') +
            " 🖥️  Command   : " + Worker.command_list_as_string(command_list),
            title=title,
            title_align="left")
        console.print(panel, style=Style(color="yellow"))
        proc = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, cwd=cwd)

        proc_stdout = proc.stdout
        fl = fcntl.fcntl(proc_stdout, fcntl.F_GETFL)
        fcntl.fcntl(proc_stdout, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        stdout_parts = []
        while proc.poll() is None:
            Worker.handle_shell_stdout(proc_stdout, stdout_parts)

        Worker.handle_shell_stdout(proc_stdout, stdout_parts)

        return stdout_parts

    @staticmethod
    def handle_shell_stdout(proc_stream, my_buffer, echo_streams=True):
        try:
            for s in iter(proc_stream.readline, b''):
                out = s.decode('utf-8').strip()
                if len(out) > 0:
                    my_buffer.append(out)
                    if echo_streams:
                        console.print(out, markup=False)
        except IOError:
            pass

    @staticmethod
    def command_list_as_string(command_list):
        s = ""
        sep = ""
        for command in command_list:
            s += command + sep
            sep = "\n"
        s = s.strip()
        if "\n" in s:
            s = "\n" + s
        return s
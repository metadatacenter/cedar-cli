import subprocess
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.WorkerType import WorkerType
from org.metadatacenter.util.GlobalContext import GlobalContext

console = Console()


class CommandOutput(list):
    """List-compatible streamed output that also preserves the process exit code."""

    def __init__(self, lines, returncode):
        super().__init__(lines)
        self.returncode = returncode


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
    def execute_generic_shell_commands(
            command_list: List[str], title: str, cwd: str = None, env=None,
            show_command: bool = True, echo_streams: bool = True,
            show_title: bool = True):
        if show_command:
            panel = Panel(
                "[yellow]" +
                ((" 📂️ Location  : " + cwd + "\n") if cwd else '') +
                " 🖥️  Command   : " + Worker.command_list_as_string(command_list),
                title=title,
                title_align="left")
            console.print(panel, style=Style(color="yellow"))
        elif show_title:
            console.print(f"[yellow]{title}[/yellow]")
        proc = subprocess.Popen(command_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, cwd=cwd,
                                executable=GlobalContext.get_shell(), env=env)

        stdout_parts = []
        Worker.handle_shell_stdout(proc.stdout, stdout_parts, echo_streams=echo_streams)
        returncode = proc.wait()

        return CommandOutput(stdout_parts, returncode)

    @staticmethod
    def handle_shell_stdout(proc_stream, my_buffer, echo_streams=True):
        for s in iter(proc_stream.readline, b''):
            out = s.decode('utf-8').strip()
            if len(out) > 0:
                my_buffer.append(out)
                if echo_streams:
                    console.print(out, markup=False)

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

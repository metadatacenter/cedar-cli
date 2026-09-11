from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.WorkerType import WorkerType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.ProcessRunner import CommandOutput, run_shell

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

    @staticmethod
    def execute_generic_shell_commands(
            command_list: List[str], title: str, cwd: str = None, env=None,
            show_command: bool = True, echo_streams: bool = True,
            show_title: bool = True):
        if isinstance(command_list, (str, bytes)):
            raise ValueError("Shell command lists must contain separate script strings")
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
        # Each entry is one script. Run all entries in order and stop at the first
        # failure; never pass a sequence of scripts as shell positional arguments.
        output = CommandOutput([], 0)
        for script in command_list:
            result = run_shell(
                script, shell=GlobalContext.get_shell(), cwd=cwd, env=env,
                on_line=(lambda line: console.print(line, markup=False)) if echo_streams else None,
            )
            output.extend(result)
            output.returncode = result.returncode
            if result.returncode:
                break
        return output

    @staticmethod
    def command_list_as_string(command_list):
        return "\n".join(command_list)

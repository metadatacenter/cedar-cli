import subprocess
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.style import Style

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.taskexecutor.TaskExecutor import TaskExecutor
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError,
    isolated_frontend_workspace,
    require_no_frontend_runtime_collision,
)
from org.metadatacenter.util.SubprocessDiagnostics import describe_subprocess_failure
from org.metadatacenter.util.Util import Util

console = Console()


class ShellTaskExecutor(TaskExecutor):

    def __init__(self):
        super().__init__()

    def execute(self, task: PlanTask, job_progress: Progress, dry_run: bool) -> int:
        super().display_header(task, job_progress, 'yellow', "Shell task executor #" + str(task.node_id))
        return self.execute_shell_command_list(task, job_progress, dry_run)

    def execute_shell_command_list(self, task: PlanTask, job_progress: Progress, dry_run: bool) -> int:
        repo = task.repo
        # commands_to_execute = [cmd.format(repo.name) for cmd in task.command_list]
        commands_to_execute = task.command_list
        cwd = Util.get_wd(repo)
        job_progress.print(Panel(
            "[green]" +
            " 📂️ Location  : " + cwd + "\n" +
            " 🏷️️  Repo type : " + repo.repo_type + "\n" +
            " 🖥️  Commands  :\n" + "\n".join(commands_to_execute),
            title="Execute shell command list #" + str(task.node_id),
            title_align="left"),
            style=Style(color="green"))
        if not dry_run:
            try:
                parameter = getattr(task, "get_parameter", lambda _name: None)
                if parameter("isolated_frontend_build") is True:
                    with isolated_frontend_workspace(
                        Path(cwd),
                        reuse_node_modules=parameter("reuse_node_modules") is True,
                    ) as (isolated_cwd, environment, collisions):
                        if collisions:
                            processes = ", ".join(f"PID {pid}" for pid, _ in collisions)
                            job_progress.print(
                                f"Active frontend runtime(s) {processes} detected; "
                                "the build is isolated from their checkout and Angular cache."
                            )
                        return self._execute_commands(
                            task, repo, commands_to_execute, str(isolated_cwd),
                            job_progress, environment,
                        )
                if parameter("in_place_frontend_build") is True:
                    require_no_frontend_runtime_collision(Path(cwd))
                return self._execute_commands(
                    task, repo, commands_to_execute, cwd, job_progress, None,
                )
            except BuildSafetyError as error:
                job_progress.print(f"Build safety check failed: {error}", markup=False)
                return 1
        else:
            time.sleep(0.1)
        return 0

    def _execute_commands(self, task, repo, commands, cwd, job_progress, environment):
        first_failure = 0
        for command in commands:
            stdout_parts, return_code = self.execute_shell_command(
                task, repo, command, cwd, job_progress, environment=environment,
            )
            if return_code != 0:
                if first_failure == 0:
                    first_failure = return_code
                if GlobalContext.fail_on_error():
                    return return_code
        return first_failure

    def execute_shell_command(
        self, task: PlanTask, repo, command, cwd, job_progress: Progress, environment=None,
    ):
        job_progress.print(Panel(
            "[bright_cyan]" +
            " 📂️ Location  : " + cwd + "\n" +
            " 🏷️️  Repo type : " + repo.repo_type + "\n" +
            " 🖥️  Command   : " + command,
            title="Shell subprocess #" + str(task.node_id),
            title_align="left"),
            style=Style(color="bright_cyan"))
        proc = subprocess.Popen([command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, cwd=cwd,
                                executable=GlobalContext.get_shell(), env=environment)

        stdout_parts = []
        self.handle_shell_stdout(proc.stdout, stdout_parts, job_progress)

        return_code = proc.wait()
        description = describe_subprocess_failure(return_code)
        if return_code < 0 and not stdout_parts:
            description += "; the process produced no diagnostic output of its own"
        color = "green" if return_code == 0 else "red"
        msg = f"[{color}]Processing {repo.name} done: {description}."
        job_progress.print(Panel(msg, style=Style(color="green"), subtitle="Shell subprocess"))
        return stdout_parts, return_code

    @staticmethod
    def handle_shell_stdout(proc_stream, my_buffer, job_progress: Progress, echo_streams=True):
        for s in iter(proc_stream.readline, b''):
            out = s.decode('utf-8').strip()
            if len(out) > 0:
                my_buffer.append(out)
                if echo_streams:
                    job_progress.print(out, markup=False)
                job_progress.update(1, advance=1)

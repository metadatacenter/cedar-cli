import json
import sys
from datetime import timedelta
from timeit import default_timer as timer
from typing import List

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.style import Style
from rich.table import Table

from org.metadatacenter.executor.Executor import Executor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.util.CustomJSONEncoder import CustomJSONEncoder
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

console = Console()


class PlanExecutor(Executor):

    def __init__(self):
        super().__init__()

    def execute(self, plan: Plan, dry_run: bool, dump_plan: bool):
        self.number_plan_nodes(plan)
        start = timer()
        plan_json = json.dumps(plan, cls=CustomJSONEncoder, indent=4)
        plan_script = self.get_plan_script(plan)
        console.print(Panel(plan_json, style=Style(color="cyan"), title="Plan JSON"))
        console.print(Panel(plan_script, style=Style(color="cyan"), title="Plan script"))

        console.print("Saving plan files")
        json_path = Util.write_cedar_file(Util.LAST_PLAN_JSON_FILE, plan_json)
        script_path = Util.write_cedar_file(Util.LAST_PLAN_SCRIPT_FILE, plan_script)

        if dump_plan:
            console.print("Dump plan only, plan files saved:")
            console.print("JSON plan:" + json_path)
            console.print("Script plan:" + script_path)
        else:
            console.print("EXECUTE PLAN")
            self.start_long_execution(plan, dry_run)

        end = timer()
        console.print("Executed plan : " + plan.name)
        console.print("Execution time: " + str(timedelta(seconds=end - start)))

    def get_plan_script(self, plan: Plan):
        lines = []
        self.recurse_plan_script(plan, lines)
        return "\n".join(lines)

    def recurse_plan_script(self, plan: PlanTask or Plan, lines: [str]):
        if isinstance(plan, PlanTask):
            if plan.command_list is not None:
                lines.append('echo "---- repo:' + plan.repo.get_fqn() + ', plan node id:#' + str(plan.node_id) + ' ----"')
                lines.append('echo "---- name:' + plan.name + ' ----"')
                lines.append("      pushd " + Util.get_wd(plan.repo))
                lines.extend(plan.command_list)
                lines.append("      popd\n")
        for task in plan.tasks:
            self.recurse_plan_script(task, lines)

    def number_plan_nodes(self, plan: Plan):
        self.recurse_plan_nodes_for_numbering(plan, 0)

    def recurse_plan_nodes_for_numbering(self, plan: PlanTask or Plan, node_id: int) -> int:
        plan.set_node_id(node_id)
        for task in plan.tasks:
            node_id += 1
            node_id = self.recurse_plan_nodes_for_numbering(task, node_id)
        return node_id

    def start_long_execution(self, plan: Plan, dry_run: bool):
        max_depth = plan.get_max_depth()

        job_progress = Progress(
            "{task.description}",
            SpinnerColumn(),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        )
        for i in range(0, max_depth):
            job_progress.add_task("[green]Job level " + str(i), total=100)

        total = sum(task.total for task in job_progress.tasks)

        overall_progress = Progress()
        overall_task = overall_progress.add_task("All Jobs", total=int(total))

        repo_progress = Progress()
        repo_task = repo_progress.add_task("")

        progress_table = Table.grid()
        progress_table.add_row(
            Panel.fit(
                overall_progress, title="Overall Progress", border_style="green", padding=(2, 2)
            ),
            Panel.fit(
                job_progress, title="[b]Jobs", border_style="red", padding=(1, 2)
            ),
            Panel.fit(
                repo_progress, title="[b]Current Repo", border_style="blue", padding=(1, 2)
            ),
        )

        with Live(progress_table, refresh_per_second=10) as live:
            self.execute_recursively(plan, max_depth, 0, [], live, overall_progress,
                                     overall_task, job_progress, repo_progress, repo_task, dry_run)

        for a in range(0, 5):
            console.print()
        console.print(Panel(
            "[bright_green] Execution succeeded!",
            title="Execution succeeded",
            title_align="left"),
            style=Style(color="bright_green"))

    def execute_recursively(self, plan: Plan, max_depth: int, current_depth: int, task_stack: List[Plan], live, overall_progress,
                            overall_task, job_progress, repo_progress, repo_task, dry_run: bool):
        task_stack.append(plan)

        repo_progress.tasks[repo_task].description = plan.repo.name if isinstance(plan, PlanTask) else ""
        job_progress.tasks[current_depth].description = plan.name
        job_progress.tasks[current_depth].completed = 0
        job_progress.tasks[current_depth].total = 100

        for job in job_progress.tasks:
            if not job.finished:
                job_progress.advance(job.id)
        completed = sum(task.completed for task in job_progress.tasks)
        overall_progress.update(overall_task, completed=completed)

        if isinstance(plan, PlanTask):
            task_executor = GlobalContext.get_task_executor(plan.task_type)
            return_code = task_executor.execute(plan, job_progress, dry_run)
            if return_code is not None and return_code != 0:
                if GlobalContext.fail_on_error():
                    live.stop()
                    for a in range(0, 10):
                        console.print()
                    console.print(Panel(
                        "[bright_magenta] Execution halted because of an error!",
                        title="Execution halted",
                        title_align="left"),
                        style=Style(color="orange_red1"))
                    sys.exit(1)
                else:
                    job_progress.print(Panel(
                        "\n" * 5 +
                        "[bright_magenta] Execution continued, error disregarded!" +
                        "\n" * 5,
                        title="Execution continued",
                        title_align="left"),
                        style=Style(color="orange_red1"))

        for task in plan.tasks:
            self.execute_recursively(task, max_depth, current_depth + 1, task_stack, live, overall_progress,
                                     overall_task, job_progress, repo_progress, repo_task, dry_run)

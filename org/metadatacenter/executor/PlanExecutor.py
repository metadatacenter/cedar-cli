import json

from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.executor.Executor import Executor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.util.CustomJSONEncoder import CustomJSONEncoder
from org.metadatacenter.util.Util import Util

console = Console()


class PlanExecutor(Executor):

    def __init__(self):
        super().__init__()

    def execute(self, plan: Plan, dry_run: bool):
        console.print(Panel(json.dumps(plan, cls=CustomJSONEncoder, indent=4), style=Style(color="cyan"), title="Plan JSON"))
        console.print(Panel(self.get_plan_script(plan), style=Style(color="cyan"), title="Plan script"))
        if dry_run:
            console.print("Do DRY RUN")
        else:
            console.print("EXECUTE PLAN")

    def get_plan_script(self, plan: Plan):
        lines = []
        self.recurse_plan_script(plan, lines)
        return "\n".join(lines)

    def recurse_plan_script(self, plan: PlanTask, lines: [str]):
        for task in plan.tasks:
            self.recurse_plan_script(task, lines)
        if isinstance(plan, PlanTask):
            if plan.command_list is not None:
                lines.append('echo "---- ' + plan.repo.get_wd() + ' ----"')
                lines.append("pushd " + Util.get_wd(plan.repo))
                lines.extend(plan.command_list)
                lines.append("popd\n")

import json

from rich.console import Console

from org.metadatacenter.executor.Executor import Executor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.util.CustomJSONEncoder import CustomJSONEncoder

console = Console()


class PlanExecutor(Executor):

    def __init__(self):
        super().__init__()

    def execute(self, plan: Plan, dry_run: bool):
        if dry_run:
            console.print("Plan JSON:")
            console.print(json.dumps(plan, cls=CustomJSONEncoder, indent=4))
            console.print("Plan script:")
            console.print(self.get_plan_script(plan))
        else:
            console.print("DO EXECUTE PLAN")
            console.print("Plan JSON:")
            console.print(json.dumps(plan, cls=CustomJSONEncoder, indent=4))
            console.print("Plan script:")
            console.print(self.get_plan_script(plan))

    def get_plan_script(self, plan: Plan):
        lines = []
        self.recurse_plan_script(plan, lines)
        return "\n".join(lines)

    def recurse_plan_script(self, plan: PlanTask, lines: [str]):
        for task in plan.tasks:
            self.recurse_plan_script(task, lines)
        if isinstance(plan, PlanTask):
            if plan.command_list is not None:
                lines.append("pushd " + plan.repo.get_wd())
                lines.extend(plan.command_list)
                lines.append("popd\n")

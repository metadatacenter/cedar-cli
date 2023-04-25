import typer

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.ReleasePreparePlanner import ReleasePreparePlanner
from org.metadatacenter.util.GlobalContext import GlobalContext

app = typer.Typer()

release_prepare_planner = ReleasePreparePlanner()

plan_executor = PlanExecutor()

GlobalContext.start(TaskType.RELEASE_PREPARE)


@app.command("prepare")
def prepare(dry_run: bool = typer.Option(False, help="Dry run")):
    plan = Plan("Prepare release all")
    release_prepare_planner.prepare(plan)
    plan_executor.execute(plan, dry_run)


@app.command("commit")
def commit():
    pass

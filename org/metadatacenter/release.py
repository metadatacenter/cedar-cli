from typing import Annotated

import typer

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.ReleasePreparePlanner import ReleasePreparePlanner
from org.metadatacenter.planner.ReleaseRollbackPlanner import ReleaseRollbackPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

app = typer.Typer(no_args_is_help=True)

plan_executor = PlanExecutor()


@app.command("prepare")
def prepare(dry_run: bool = typer.Option(False, help="Dry run")):
    Util.check_release_variables()
    GlobalContext.mark_global_task_type(TaskType.RELEASE_PREPARE)
    plan = Plan("Prepare release all")
    ReleasePreparePlanner.prepare(plan)
    plan_executor.execute(plan, dry_run)


@app.command("rollback", no_args_is_help=True)
def rollback(branch: Annotated[str, typer.Option(help="Branch to delete")],
             tag: Annotated[str, typer.Option(help="Tag to delete")],
             dry_run: bool = typer.Option(False, help="Dry run")):
    Util.mark_rollback_branch(branch)
    Util.mark_rollback_tag(tag)
    GlobalContext.mark_global_task_type(TaskType.RELEASE_ROLLBACK)
    plan = Plan("Prepare rollback release all")
    ReleaseRollbackPlanner.rollback(plan)
    plan_executor.execute(plan, dry_run)


def commit(dry_run: bool = typer.Option(False, help="Dry run")):
    Util.check_release_variables()
    GlobalContext.mark_global_task_type(TaskType.RELEASE_COMMIT)
    plan = Plan("Commit prepared release all")
    # ReleasePreparePlanner.prepare(plan)
    # plan_executor.execute(plan, dry_run)


@app.command("commit")
def commit():
    pass

import typer

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext

app = typer.Typer(no_args_is_help=True)

build_planner = BuildPlanner()

plan_executor = PlanExecutor()


@app.command("this")
def this(
        dry_run: bool = typer.Option(False, help="Dry run"),
        wd: str = typer.Option(None, help="Working directory")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build this")
    build_planner.this(plan, wd)
    plan_executor.execute(plan, dry_run)


@app.command("parent")
def parent(dry_run: bool = typer.Option(False, help="Dry run")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build parent")
    build_planner.parent(plan)
    plan_executor.execute(plan, dry_run)


@app.command("libraries")
def libraries(dry_run: bool = typer.Option(False, help="Dry run")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build libraries")
    build_planner.libraries(plan)
    plan_executor.execute(plan, dry_run)


@app.command("project")
def project(dry_run: bool = typer.Option(False, help="Dry run")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build project")
    build_planner.project(plan)
    plan_executor.execute(plan, dry_run)


@app.command("clients")
def clients(dry_run: bool = typer.Option(False, help="Dry run")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build clients")
    build_planner.clients(plan)
    plan_executor.execute(plan, dry_run)


@app.command("java")
def java(dry_run: bool = typer.Option(False, help="Dry run")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build java")
    build_planner.parent(plan)
    build_planner.libraries(plan)
    build_planner.project(plan)
    build_planner.clients(plan)
    plan_executor.execute(plan, dry_run)


@app.command("frontends")
def frontends(dry_run: bool = typer.Option(False, help="Dry run")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build frontends")
    build_planner.frontends(plan)
    plan_executor.execute(plan, dry_run)


@app.command("all")
def all(dry_run: bool = typer.Option(False, help="Dry run")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build all")
    build_planner.parent(plan)
    build_planner.libraries(plan)
    build_planner.project(plan)
    build_planner.clients(plan)
    build_planner.frontends(plan)
    plan_executor.execute(plan, dry_run)

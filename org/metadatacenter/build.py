import typer

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext

app = typer.Typer(no_args_is_help=True)

plan_executor = PlanExecutor()


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory"),
         dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build this")
    BuildPlanner.this(plan, wd)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("parent")
def parent(dry_run: bool = typer.Option(False, help="Dry run"),
           dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build parent")
    BuildPlanner.parent(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("libraries")
def libraries(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build libraries")
    BuildPlanner.libraries(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("project")
def project(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build project")
    BuildPlanner.project(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("clients")
def clients(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build clients")
    BuildPlanner.clients(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("java")
def java(dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build java")
    BuildPlanner.parent(plan)
    BuildPlanner.libraries(plan)
    BuildPlanner.project(plan)
    BuildPlanner.clients(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("frontends")
def frontends(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build frontends")
    BuildPlanner.frontends(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("all")
def build_all(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build all")
    BuildPlanner.parent(plan)
    BuildPlanner.libraries(plan)
    BuildPlanner.project(plan)
    BuildPlanner.clients(plan)
    BuildPlanner.frontends(plan)
    plan_executor.execute(plan, dry_run, dump_plan)

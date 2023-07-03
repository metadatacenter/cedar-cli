import typer

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.DeployPlanner import DeployPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext

app = typer.Typer(no_args_is_help=True)

plan_executor = PlanExecutor()


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory"),
         dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan")
         ):
    GlobalContext.mark_global_task_type(TaskType.DEPLOY)
    plan = Plan("Deploy this")
    DeployPlanner.this(plan, wd)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("parent")
def parent(dry_run: bool = typer.Option(False, help="Dry run"),
           dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.DEPLOY)
    plan = Plan("Deploy parent")
    DeployPlanner.parent(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("libraries")
def libraries(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.DEPLOY)
    plan = Plan("Deploy libraries")
    DeployPlanner.libraries(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("project")
def project(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.DEPLOY)
    plan = Plan("Deploy project")
    DeployPlanner.project(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("clients")
def clients(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.DEPLOY)
    plan = Plan("Deploy clients")
    DeployPlanner.clients(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("java")
def java(dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.DEPLOY)
    plan = Plan("Deploy java")
    DeployPlanner.parent(plan)
    DeployPlanner.libraries(plan)
    DeployPlanner.project(plan)
    DeployPlanner.clients(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("frontends")
def frontends(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.DEPLOY)
    plan = Plan("Deploy frontends")
    DeployPlanner.frontends(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("all")
def deploy_all(dry_run: bool = typer.Option(False, help="Dry run"),
               dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.DEPLOY)
    plan = Plan("Deploy all")
    DeployPlanner.all(plan)
    plan_executor.execute(plan, dry_run, dump_plan)

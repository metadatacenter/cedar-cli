import typer

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.PublishPlanner import PublishPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.BuildTrainWorker import BuildTrainWorker

app = typer.Typer(no_args_is_help=True)

plan_executor = PlanExecutor()


@app.command("train")
def train(
        resume: str = typer.Option(
            None,
            "--resume",
            help="Resume an incomplete train from its recorded source manifest.",
        )):
    """Publish an ordered, immutable Maven and Docker build train."""
    raise typer.Exit(code=BuildTrainWorker.dispatch(resume=resume))


@app.command("train-status")
def train_status(version: str = typer.Argument(..., help="Immutable train identifier.")):
    """Show which persisted Maven, npm, and Docker train stages are recorded."""
    raise typer.Exit(code=BuildTrainWorker.status(version))


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory"),
         dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan")
         ):
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish this")
    PublishPlanner.this(plan, wd)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("parent")
def parent(dry_run: bool = typer.Option(False, help="Dry run"),
           dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish parent")
    PublishPlanner.parent(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("libraries")
def libraries(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish libraries")
    PublishPlanner.libraries(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("project")
def project(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish project")
    PublishPlanner.project(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("clients")
def clients(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish clients")
    PublishPlanner.clients(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("java")
def java(dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish java")
    PublishPlanner.parent(plan)
    PublishPlanner.libraries(plan)
    PublishPlanner.project(plan)
    PublishPlanner.clients(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("frontends")
def frontends(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish frontends")
    PublishPlanner.frontends(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("split-frontends")
def split_frontends(dry_run: bool = typer.Option(False, help="Dry run"),
                    dump_plan: bool = typer.Option(False, help="Dump plan")):
    """Publish Workspace and Template Designer npm packages to their configured Nexus registry."""
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish split frontends")
    PublishPlanner.split_frontends(plan)
    plan_executor.execute(plan, dry_run, dump_plan)


@app.command("all")
def publish_all(dry_run: bool = typer.Option(False, help="Dry run"),
                dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.PUBLISH)
    plan = Plan("Publish all")
    PublishPlanner.all(plan)
    plan_executor.execute(plan, dry_run, dump_plan)

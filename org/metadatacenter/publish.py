import typer

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.PublishPlanner import PublishPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.BuildTrainWorker import BuildTrainWorker
from org.metadatacenter.worker.LockBaselineWorker import LockBaselineWorker

app = typer.Typer(no_args_is_help=True)

plan_executor = PlanExecutor()


@app.command("train")
def train(
        resume: str = typer.Option(
            None,
            "--resume",
            help="Resume an incomplete train from its recorded source manifest.",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Validate and show the dispatch without starting a workflow.",
        )):
    """Publish an ordered, immutable Maven, npm, and Docker build train."""
    raise typer.Exit(code=BuildTrainWorker.dispatch(resume=resume, dry_run=dry_run))


@app.command("train-status")
def train_status(
        version: str = typer.Argument(
            None,
            help="Immutable train identifier. Defaults to the newest dispatched train.",
        ),
        watch: bool = typer.Option(
            False,
            "--watch",
            help="Follow compact major-stage and Docker-matrix counts until completion.",
        )):
    """Show persisted stages, workflow progress, and the safe recovery decision."""
    raise typer.Exit(code=BuildTrainWorker.status(version, watch=watch))


@app.command("baselines")
def baselines(
        refresh: bool = typer.Option(
            False,
            "--refresh",
            help="Recompute the digest and npm audit counts of every stale lock and write them "
                 "to frontend-train.json for review.",
        ),
        repository: list[str] = typer.Option(
            None,
            "--repository",
            help="Only this repository's locks; may be repeated.",
        ),
        show_all: bool = typer.Option(
            False, "--all", help="List every baseline, not only the stale ones.",
        )):
    """Show which reviewed npm lock baselines no longer match their lockfiles, or refresh them."""
    if refresh:
        raise typer.Exit(code=LockBaselineWorker.refresh(repositories=repository))
    raise typer.Exit(code=LockBaselineWorker.report(show_all=show_all, repositories=repository))


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

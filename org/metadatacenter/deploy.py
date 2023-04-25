import typer

from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.DeployPlanner import DeployPlanner
from org.metadatacenter.util.GlobalContext import GlobalContext

app = typer.Typer()

deploy_planner = DeployPlanner()

plan_executor = PlanExecutor()

GlobalContext.start(TaskType.DEPLOY)


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory"),
         dry_run: bool = typer.Option(False, help="Dry run")
         ):
    plan = Plan("Deploy this")
    deploy_planner.this(plan, wd)
    plan_executor.execute(plan, dry_run)


@app.command("parent")
def parent(dry_run: bool = typer.Option(False, help="Dry run")):
    plan = Plan("Deploy parent")
    deploy_planner.parent(plan)
    plan_executor.execute(plan, dry_run)


@app.command("libraries")
def libraries(dry_run: bool = typer.Option(False, help="Dry run")):
    plan = Plan("Deploy libraries")
    deploy_planner.libraries(plan)
    plan_executor.execute(plan, dry_run)


@app.command("project")
def project(dry_run: bool = typer.Option(False, help="Dry run")):
    plan = Plan("Deploy project")
    deploy_planner.project(plan)
    plan_executor.execute(plan, dry_run)


@app.command("clients")
def clients(dry_run: bool = typer.Option(False, help="Dry run")):
    plan = Plan("Deploy clients")
    deploy_planner.clients(plan)
    plan_executor.execute(plan, dry_run)


@app.command("java")
def java(dry_run: bool = typer.Option(False, help="Dry run")):
    plan = Plan("Deploy java")
    deploy_planner.parent(plan)
    deploy_planner.libraries(plan)
    deploy_planner.project(plan)
    deploy_planner.clients(plan)
    plan_executor.execute(plan, dry_run)


@app.command("frontends")
def frontends(dry_run: bool = typer.Option(False, help="Dry run")):
    plan = Plan("Deploy frontends")
    deploy_planner.frontends(plan)
    plan_executor.execute(plan, dry_run)


@app.command("all")
def all(dry_run: bool = typer.Option(False, help="Dry run")):
    plan = Plan("Deploy all")
    deploy_planner.parent(plan)
    deploy_planner.libraries(plan)
    deploy_planner.project(plan)
    deploy_planner.clients(plan)
    deploy_planner.frontends(plan)
    plan_executor.execute(plan, dry_run)

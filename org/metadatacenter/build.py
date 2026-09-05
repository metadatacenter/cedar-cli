from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from org.metadatacenter import maven
from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.util.BuildSafety import capture_estate_state, changed_repositories
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

app = typer.Typer(no_args_is_help=True)
app.add_typer(maven.app, name="maven", help="Maven cache operations...")

plan_executor = PlanExecutor()
console = Console()

JAVA_TESTS_OPTION_HELP = "Run Java test suites. Default: run."


def configure_java_tests(tests: bool):
    GlobalContext.mark_skip_tests(not tests)


def execute_build(plan: Plan, dry_run: bool, dump_plan: bool):
    """Run a build while proving it did not add or alter tracked workspace changes."""
    if dry_run or dump_plan:
        return plan_executor.execute(plan, dry_run, dump_plan)
    before = capture_estate_state(Path(Util.cedar_home))
    failure = None
    try:
        plan_executor.execute(plan, dry_run, dump_plan)
    except BaseException as error:
        failure = error
    after = capture_estate_state(Path(Util.cedar_home))
    changed = changed_repositories(before, after)
    if changed:
        names = ", ".join(path.name for path in changed)
        console.print(Panel(
            "The build changed tracked state relative to its starting snapshot: " + names,
            title="Build workspace invariant failed",
            style="red",
        ))
        raise SystemExit(1) from failure
    if failure is not None:
        raise failure


@app.command("this")
def this(wd: str = typer.Option(None, help="Working directory"),
         dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan"),
         tests: bool = typer.Option(
             True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build this")
    BuildPlanner.this(plan, wd)
    execute_build(plan, dry_run, dump_plan)


@app.command("parent")
def parent(dry_run: bool = typer.Option(False, help="Dry run"),
           dump_plan: bool = typer.Option(False, help="Dump plan"),
           tests: bool = typer.Option(
               True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build parent")
    BuildPlanner.parent(plan)
    execute_build(plan, dry_run, dump_plan)


@app.command("libraries")
def libraries(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan"),
              tests: bool = typer.Option(
                  True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build libraries")
    BuildPlanner.libraries(plan)
    execute_build(plan, dry_run, dump_plan)


@app.command("project")
def project(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan"),
            tests: bool = typer.Option(
                True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build project")
    BuildPlanner.project(plan)
    execute_build(plan, dry_run, dump_plan)


@app.command("clients")
def clients(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan"),
            tests: bool = typer.Option(
                True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build clients")
    BuildPlanner.clients(plan)
    execute_build(plan, dry_run, dump_plan)


@app.command("java")
def java(dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan"),
         tests: bool = typer.Option(
             True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build java")
    BuildPlanner.parent(plan)
    BuildPlanner.libraries(plan)
    BuildPlanner.project(plan)
    BuildPlanner.clients(plan)
    execute_build(plan, dry_run, dump_plan)


@app.command("frontends")
def frontends(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan")):
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build frontends")
    BuildPlanner.frontends(plan)
    execute_build(plan, dry_run, dump_plan)


@app.command("split-frontends")
def split_frontends(dry_run: bool = typer.Option(False, help="Dry run"),
                    dump_plan: bool = typer.Option(False, help="Dump plan"),
                    server_payload: bool = typer.Option(
                        False, "--server-payload",
                        help="Generate environment-configured static payloads for native nginx")):
    """Build Workspace and Template Designer from their native Git checkouts."""
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build split frontends")
    BuildPlanner.split_frontends(plan, server_payload=server_payload)
    execute_build(plan, dry_run, dump_plan)


@app.command("all")
def build_all(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan"),
              tests: bool = typer.Option(
                  True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build all")
    BuildPlanner.parent(plan)
    BuildPlanner.libraries(plan)
    BuildPlanner.project(plan)
    BuildPlanner.clients(plan)
    BuildPlanner.frontends(plan)
    execute_build(plan, dry_run, dump_plan)

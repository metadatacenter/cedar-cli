from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from org.metadatacenter import maven, reactor
from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.util.BuildSafety import capture_estate_state, changed_repositories, tracked_path_state
from org.metadatacenter.util.BuildSafety import BuildSafetyError
from org.metadatacenter.util.NodeBuildCheck import require_plan_node
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

app = typer.Typer(no_args_is_help=True)
app.add_typer(maven.app, name="maven", help="Maven cache operations...")

plan_executor = PlanExecutor()
console = Console()

JAVA_TESTS_OPTION_HELP = "Run Java test suites. Default: run."


def configure_java_tests(tests: bool):
    GlobalContext.mark_skip_tests(not tests)


def frontend_input_roots(plan):
    """Frontend inputs and their build tooling; unrelated backend work is independent."""
    home = Path(Util.cedar_home)
    roots = {home / 'cedar-cli', home / 'cedar-development'}
    def visit(task):
        repo = getattr(task, 'repo', None)
        if repo is not None:
            while getattr(repo, 'parent_repo', None) is not None:
                repo = repo.parent_repo
            roots.add(home / repo.name)
        for child in task.tasks:
            visit(child)
    visit(plan)
    return roots


# Profiles and frontend configuration live in this mixed-purpose repository.
# Backend audits, repairs and documentation are not frontend build inputs.
FRONTEND_DEVELOPMENT_INPUTS = (
    'bin', 'ops/frontend-train.json', 'ops/cedar-services.sh',
)


def capture_build_state(home: Path, frontend_only: bool):
    state = capture_estate_state(home)
    development = (home / 'cedar-development').resolve()
    if frontend_only and development in state:
        state[development] = tracked_path_state(development, FRONTEND_DEVELOPMENT_INPUTS)
    return state


def execute_build(plan: Plan, dry_run: bool, dump_plan: bool, *, frontend_only=False):
    """Run a build while proving it did not add or alter tracked workspace changes."""
    if dry_run or dump_plan:
        return plan_executor.execute(plan, dry_run, dump_plan)
    try:
        require_plan_node(plan)
    except BuildSafetyError as error:
        console.print(str(error), markup=False)
        raise typer.Exit(code=1) from error
    input_roots = frontend_input_roots(plan) if frontend_only else None
    before = capture_build_state(Path(Util.cedar_home), frontend_only)
    failure = None
    try:
        with reactor.session_for_plan(Util.cedar_home, plan):
            plan_executor.execute(plan, dry_run, dump_plan)
            selection = reactor.runtime_selection(Util.cedar_home)
    except BaseException as error:
        failure = error
    after = capture_build_state(Path(Util.cedar_home), frontend_only)
    changed = changed_repositories(before, after)
    if input_roots is not None:
        changed = [path for path in changed if path in input_roots]
    if changed:
        names = ", ".join(path.name for path in changed)
        console.print(Panel(
            "Tracked build inputs changed during the build (by this build or concurrent work): " + names,
            title="Build workspace invariant failed",
            style="red",
        ))
        raise SystemExit(1) from failure
    if failure is not None:
        raise failure
    return selection


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
    selection = execute_build(plan, dry_run, dump_plan, frontend_only=True)
    if not dry_run and not dump_plan:
        reactor.activate_runtime(Util.cedar_home, selection)
        console.print("Local frontend starts will use this reactor build's component artifacts.")


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

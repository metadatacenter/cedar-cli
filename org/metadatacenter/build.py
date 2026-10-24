from pathlib import Path
from typing import Optional
import json
import subprocess
import sys
import time
from xml.etree import ElementTree

import typer
from rich.console import Console
from rich.panel import Panel

from org.metadatacenter import maven, reactor, reactor_evidence
from org.metadatacenter.coverage import report_coverage
from org.metadatacenter.design_tokens import check_design_tokens
from org.metadatacenter.executor.PlanExecutor import PlanExecutor
from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.BuildPlanner import BuildPlanner
from org.metadatacenter.util.BuildSafety import capture_estate_state, changed_repositories, tracked_path_state
from org.metadatacenter.util.BuildSafety import BuildSafetyError
from org.metadatacenter.util.NodeBuildCheck import require_plan_node
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.InvocationContext import default_build_jobs, default_build_workers, default_maven_threads
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.NativeWorker import NativeWorker
from org.metadatacenter.smoke_gate import run_smoke

app = typer.Typer(no_args_is_help=True)
app.add_typer(maven.app, name="maven", help="Maven cache operations...")

plan_executor = PlanExecutor()
console = Console()

@app.callback()
def concurrency(
    jobs: Optional[int] = typer.Option(
        None, min=1, max=16,
        help="Concurrent frontend repositories and Maven reactor threads; 1 is serial. "
             "Default: three repositories on 16+ cores, otherwise two; Maven uses up to 16 cores."),
    workers: Optional[int] = typer.Option(None, min=1, max=16, help="Workers per frontend: 12 on 16+ cores, otherwise half the cores up to 8. The host pool bounds total concurrency."),
):
    from org.metadatacenter.util.InvocationContext import current_context
    # Concurrent frontend builds multiply by their worker budget, so only Maven follows the host.
    current_context().settings.build_jobs = jobs or default_build_jobs()
    current_context().settings.maven_threads = jobs or default_maven_threads()
    current_context().settings.build_workers = workers or default_build_workers()


JAVA_TESTS_OPTION_HELP = "Run Java test suites. Default: run."


def configure_java_tests(tests: bool):
    GlobalContext.mark_skip_tests(not tests)


def require_integration_tags(tests: bool, dry_run: bool, dump_plan: bool):
    """Refuse a Java build that would run an untagged integration class.

    The tag changes no default run, so Maven cannot notice it missing. Reading the test sources
    takes about a second, so the check runs before any Maven work rather than after it.
    """
    if not tests or dry_run or dump_plan:
        return
    from org.metadatacenter.worker.IntegrationTagWorker import REMEDY, IntegrationTagWorker
    findings = IntegrationTagWorker.findings()
    if not findings:
        return
    console.print(f"[red]{len(findings)} Java test classes start a server or store without the "
                  "integration tag:[/red]")
    for finding in findings:
        console.print(f"  {finding}", markup=False, highlight=False)
    console.print(REMEDY)
    raise typer.Exit(1)


def require_design_token_adoption(verify: bool, dry_run: bool, dump_plan: bool):
    """Apply CI's style policy against the dependencies this source build will supply."""
    if not verify or dry_run or dump_plan:
        return
    from org.metadatacenter.frontend_inventory import inventory
    module, config = inventory()
    surfaces = module.surfaces(config)
    # Only consumers built with the local token producer may use its source vocabulary.
    # Vendored copies (including MCP) and standalone CI retain their registry checks.
    source_consumers = (sorted({row['repository'] for row in surfaces})
                        if any(row['reactorName'] == 'cedar-design-tokens' for row in surfaces) else [])
    code = check_design_tokens(strict=True, source_consumers=source_consumers)
    if code:
        console.print("[red]The design-token adoption check failed for this source build.[/red] "
                      "Fix the findings above, then build again.")
        raise typer.Exit(code)


def _local_name(element):
    return element.tag.rsplit('}', 1)[-1]


def maven_modules(checkout: Path):
    """The modules a checkout's POM builds by default, as directories.

    Only the project's own <modules> count: a profile's modules are built only when it is active,
    and a commented-out module is not built at all, which is why the POM is parsed rather than
    searched.
    """
    try:
        project = ElementTree.parse(checkout / 'pom.xml').getroot()
    except (OSError, ElementTree.ParseError):
        return []
    return [(checkout / module.text.strip()).resolve()
            for modules in project if _local_name(modules) == 'modules'
            for module in modules if _local_name(module) == 'module' and (module.text or '').strip()]


def aggregated_checkouts(checkout: Path, home: Path):
    """The other checkouts an aggregator builds through `../` modules, followed transitively.

    cedar-project, for one, compiles cedar-artifact-server and every other server from their own
    checkouts, and cedar-microservice-libraries among them aggregates further.
    """
    found, pending = set(), [checkout]
    while pending:
        for module in maven_modules(pending.pop()):
            try:
                member = home / module.relative_to(home).parts[0]
            except (ValueError, IndexError):
                continue  # A module outside CEDAR_HOME, or CEDAR_HOME itself, is no checkout.
            if member != checkout and member not in found:
                found.add(member)
                pending.append(member)
    return found


def build_input_roots(plan):
    """The repositories a build reads: those its plan builds, and the build tooling.

    Work elsewhere in the estate is not an input, for a Java build as for a frontend one, so an edit
    to an unrelated repository does not fail a build that never read it. A checkout an aggregator
    builds through a `../` module is an input, although the plan names only the aggregator. The
    roots are resolved, because the captured state is keyed by resolved paths and a symlinked
    CEDAR_HOME otherwise matches nothing, which silently disables the invariant.
    """
    home = Path(Util.cedar_home).resolve()
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
    roots = {root.resolve() for root in roots}
    for root in list(roots):
        roots |= aggregated_checkouts(root, home)
    return roots


# Profiles and frontend configuration live in this mixed-purpose repository.
# Backend audits, repairs and documentation are not build inputs, for Java either.
FRONTEND_DEVELOPMENT_INPUTS = (
    'bin', 'ops/frontend-train.json', 'ops/frontend_inventory.py',
    'ops/cedar-services.sh', 'ops/frontend_reactor_runtime.py', 'ops/frontend_ready.py',
    'ops/build-native-split-frontend.sh', 'ops/write-native-frontend-build-info.mjs',
)


def capture_build_state(home: Path, frontend_only: bool):
    state = capture_estate_state(home)
    development = (home / 'cedar-development').resolve()
    if development in state:
        state[development] = tracked_path_state(development, FRONTEND_DEVELOPMENT_INPUTS)
    return state


def execute_java_build(plan: Plan, dry_run: bool, dump_plan: bool):
    started = time.time()
    selection = execute_build(plan, dry_run, dump_plan)
    if not GlobalContext.should_skip_tests() and not dry_run and not dump_plan:
        report_coverage(started)
    return selection


def execute_build(plan: Plan, dry_run: bool, dump_plan: bool, *, frontend_only=False):
    """Run a build while proving it did not add or alter tracked workspace changes."""
    from org.metadatacenter.util.InvocationContext import current_context
    from org.metadatacenter.frontend_snapshot import prepare
    try:
        prepare(plan)
    except reactor.ReactorError as error:
        console.print(str(error), markup=False)
        raise typer.Exit(1) from error
    if getattr(plan, 'frontend_snapshot', False):
        console.print('Frontend dependencies: current-source SNAPSHOTs, including uncommitted edits; '
                      'no registry-pin fallback.')
    plan.build_jobs = current_context().settings.build_jobs
    if dry_run or dump_plan:
        return plan_executor.execute(plan, dry_run, dump_plan)
    try:
        require_plan_node(plan)
    except BuildSafetyError as error:
        console.print(str(error), markup=False)
        raise typer.Exit(code=1) from error
    input_roots = build_input_roots(plan)
    before = capture_build_state(Path(Util.cedar_home), frontend_only)
    failure = None
    try:
        with reactor_evidence.session(plan), reactor.session_for_plan(Util.cedar_home, plan):
            plan_executor.execute(plan, dry_run, dump_plan)
            selection = (reactor.runtime_selection(Util.cedar_home)
                         if getattr(plan, 'frontend_snapshot', False) else {})
            if frontend_only:
                selection = reactor_evidence.Selection(
                    selection, reactor_evidence.finish(Util.cedar_home, selection))
    except BaseException as error:
        failure = error
    after = capture_build_state(Path(Util.cedar_home), frontend_only)
    changed = [path for path in changed_repositories(before, after) if path in input_roots]
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
    execute_java_build(plan, dry_run, dump_plan)


@app.command("parent")
def parent(dry_run: bool = typer.Option(False, help="Dry run"),
           dump_plan: bool = typer.Option(False, help="Dump plan"),
           tests: bool = typer.Option(
               True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build parent")
    BuildPlanner.parent(plan)
    execute_java_build(plan, dry_run, dump_plan)


@app.command("libraries")
def libraries(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan"),
              tests: bool = typer.Option(
                  True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build libraries")
    BuildPlanner.libraries(plan)
    execute_java_build(plan, dry_run, dump_plan)


@app.command("project")
def project(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan"),
            tests: bool = typer.Option(
                True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build project")
    BuildPlanner.project(plan)
    execute_java_build(plan, dry_run, dump_plan)


@app.command("clients")
def clients(dry_run: bool = typer.Option(False, help="Dry run"),
            dump_plan: bool = typer.Option(False, help="Dump plan"),
            tests: bool = typer.Option(
                True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build clients")
    BuildPlanner.clients(plan)
    execute_java_build(plan, dry_run, dump_plan)


@app.command("java")
def java(dry_run: bool = typer.Option(False, help="Dry run"),
         dump_plan: bool = typer.Option(False, help="Dump plan"),
         tests: bool = typer.Option(
             True, "--tests/--skip-tests", help=JAVA_TESTS_OPTION_HELP)):
    configure_java_tests(tests)
    require_integration_tags(tests, dry_run, dump_plan)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build java")
    BuildPlanner.parent(plan)
    BuildPlanner.libraries(plan)
    BuildPlanner.project(plan)
    BuildPlanner.clients(plan)
    execute_java_build(plan, dry_run, dump_plan)


@app.command("frontends")
def frontends(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan"),
              reuse: bool = typer.Option(
                  True, "--reuse/--no-reuse",
                  help="Take a repository's earlier passing result when everything it reads is "
                       "unchanged. --no-reuse runs every repository's checks again.")):
    run_frontends(dry_run=dry_run, dump_plan=dump_plan, reuse=reuse, deploy=True)


def run_frontends(*, dry_run=False, dump_plan=False, reuse=True, deploy=False):
    """Shared source verification; deployment is exclusively the full build command's contract."""
    from org.metadatacenter.cee_demos import check_selection
    from org.metadatacenter.frontend_timings import frontend_timings
    from org.metadatacenter.util.InvocationContext import current_context
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build frontends" if deploy else "Check frontends")
    BuildPlanner.frontends(plan, reuse=reuse)
    if dry_run or dump_plan:
        return execute_build(plan, dry_run, dump_plan, frontend_only=True)
    with frontend_timings(Util.cedar_home, 'reactor' if deploy else 'checks') as timing:
        with timing.phase('design-token preflight'):
            require_design_token_adoption(True, False, False)
        with timing.phase('build and verification'):
            selection = execute_build(plan, False, False, frontend_only=True)
        with timing.phase('consumer demos'):
            check_selection(selection)
        console.print(f"Reactor build evidence: {reactor.store_root(Util.cedar_home) / 'builds' / (selection.evidence + '.json')}")
        if not deploy:
            console.print("Frontend checks passed. No runtime was activated or restarted; "
                          "whole-stack acceptance requires cedarcli build frontends.")
            return selection
        with timing.phase('local deployment'):
            reactor.activate_runtime(Util.cedar_home, selection)
            console.print("Restarting local frontends with this reactor's exact component selection.")
            result = NativeWorker.restart(NativeWorker.FRONTENDS, ready_through_nginx=True)
            if result.returncode:
                raise typer.Exit(result.returncode)
        with timing.phase('served bundle verification'):
            home = Path(Util.cedar_home)
            record = json.loads((home / '.reactor/builds' / (selection.evidence + '.json')).read_text())
            config = json.loads((home / 'cedar-development/ops/frontend-train.json').read_text())
            applications = {frontend['repository'] for frontend in config['frontends']}
            for repository in record['repositories']:
                if Path(repository).parts[0] not in applications:
                    continue
                subprocess.run([sys.executable, str(home / 'cedar-development/ops/frontend_reactor_runtime.py'),
                                'verify', str(home), str(home / repository)], check=True)
        with timing.phase('whole-stack smoke'):
            code = run_smoke(rest_workers=4 if current_context().settings.build_workers >= 12 else None)
            if code:
                raise typer.Exit(code)
        console.print("Frontend reactor completed: build, local deployment and whole-stack smoke passed.")


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


@app.command("server-frontends")
def server_frontends(dry_run: bool = typer.Option(False, help="Dry run"),
                     dump_plan: bool = typer.Option(False, help="Dump plan"),
                     server_payload: bool = typer.Option(
                         False, "--server-payload",
                         help="Generate environment-configured static payloads for native nginx")):
    """Build every frontend a native host serves from its own checkout, monolith included."""
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build server frontends")
    BuildPlanner.server_frontends(plan, server_payload=server_payload)
    execute_build(plan, dry_run, dump_plan)


@app.command("all")
def build_all(dry_run: bool = typer.Option(False, help="Dry run"),
              dump_plan: bool = typer.Option(False, help="Dump plan"),
              tests: bool = typer.Option(
                  True, "--tests/--skip-tests", help="Run Java and frontend verification suites. Default: run.")):
    configure_java_tests(tests)
    require_integration_tags(tests, dry_run, dump_plan)
    require_design_token_adoption(tests, dry_run, dump_plan)
    GlobalContext.mark_global_task_type(TaskType.BUILD)
    plan = Plan("Build all")
    BuildPlanner.parent(plan)
    BuildPlanner.libraries(plan)
    BuildPlanner.project(plan)
    BuildPlanner.clients(plan)
    BuildPlanner.frontends(plan, verification=tests)
    selection = execute_java_build(plan, dry_run, dump_plan)
    if tests and not dry_run and not dump_plan:
        from org.metadatacenter.cee_demos import check_selection
        check_selection(selection)


@app.command("diagnostics")
def diagnostics(
    days: int = typer.Option(
        14, min=0, help="Retain completed failure bundles and coverage records younger than this many days"),
    max_mib: int = typer.Option(1024, min=0, help="Total retained diagnostic bundle budget in MiB"),
    apply: bool = typer.Option(False, "--apply", help="Delete selected entries; default only previews"),
):
    """Preview or apply retention to failed-build diagnostics and to coverage records."""
    from org.metadatacenter.build_diagnostics import prune_failures
    from org.metadatacenter.coverage import prune_coverage
    from org.metadatacenter.util.InvocationContext import invocation_environment
    home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
    if not home:
        console.print('CEDAR_HOME is not set')
        raise typer.Exit(1)
    try:
        rows = prune_failures(home, days=days, max_bytes=max_mib * 1024 * 1024, apply=apply)
        for row in rows:
            console.print(f"{row['action']}: {row['path']} ({row['bytes']} bytes; {row['reason']})",
                          markup=False, soft_wrap=True)
        console.print(f"{len(rows)} recognized completed bundles. Unrecognized, incomplete and symlinked entries are left alone.")
        records = prune_coverage(home, days=days, apply=apply)
        for row in records:
            console.print(f"{row['action']}: {row['path']} ({row['bytes']} bytes; {row['reason']})",
                          markup=False, soft_wrap=True)
        console.print(f"{len(records)} recognized coverage records. Unrecognized and symlinked entries are left alone.")
        if not apply:
            console.print('Preview only; add --apply to remove the selected bundles and records.')
    except (OSError, ValueError) as error:
        console.print(str(error), markup=False)
        raise typer.Exit(1) from error

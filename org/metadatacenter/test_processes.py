import os
import signal
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from org.metadatacenter.smoke_gate import run_smoke
from org.metadatacenter.util.BuildSafety import embedded_mongo_processes


app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("cee-demos")
def cee_demos(cee_package: Optional[Path] = typer.Option(
        None, help="Test a staged CEE package directory or tarball; default tests the demos' checked-out pins.")):
    """Always lint, test and build the Angular, React and Ember CEE demos in isolated copies."""
    from org.metadatacenter.cee_demos import run_demos
    from org.metadatacenter.util.BuildSafety import BuildSafetyError
    try:
        raise typer.Exit(run_demos(cee_package))
    except BuildSafetyError as error:
        console.print(str(error), markup=False)
        raise typer.Exit(1) from error


@app.command("e2e")
def e2e(rest_workers: int = typer.Option(None, min=1, max=4,
        help="Concurrent independent REST suites; 1 preserves serial order (default: 2)."),
        split_lanes: int = typer.Option(None, min=1, max=4,
        help="General lanes of split-tier journeys beside its core and identity lanes; "
             "1 runs the tier sequentially (default: 2)."),
        split_timeout_cap: int = typer.Option(None, min=60, max=7200,
        help="Seconds no split-tier unit may run past, whatever its measured duration; "
             "also the deadline of a unit no report has measured (default: 1200).")):
    """Run every whole-stack smoke tier and record the evidence the train and release gates require."""
    raise typer.Exit(run_smoke(rest_workers=rest_workers, split_lanes=split_lanes,
                               split_timeout_cap=split_timeout_cap))


@app.command("search")
def search(plan: bool = typer.Option(False, help="Print the offline corpus and expanded matrix inventory (the default)."),
           check: bool = typer.Option(False, help="Run offline harness tests with networking disabled."),
           validate: bool = typer.Option(False, help="Validate artifacts offline using the prebuilt local Java 17 validator/reader."),
           live: bool = typer.Option(False, help="Explicitly create temporary corpus artifacts and exercise the running stack."),
           isolated: bool = typer.Option(False, help="Use unwrapped queries; requires an otherwise isolated test deployment."),
           tier: str = typer.Option("rest", help="rest, journey or all"),
           report: Optional[Path] = typer.Option(None, help="Live evidence/cleanup ledger; defaults under .cedar/reports/search.")):
    """Prepare or explicitly run the opt-in search corpus; never builds or restarts the stack."""
    from org.metadatacenter.search_harness import run_search
    try:
        raise typer.Exit(run_search(plan=plan, check=check, validate=validate, live=live,
                                    isolated=isolated, tier=tier, report=report))
    except ValueError as error:
        console.print(str(error), markup=False)
        raise typer.Exit(2) from error


@app.command("status")
def status():
    """List embedded MongoDB processes that can interfere with backend tests."""
    processes = embedded_mongo_processes()
    if not processes:
        console.print("No embedded Mongo test processes are running.")
        return
    for process in processes:
        console.print(process.describe(), markup=False)


@app.command("cleanup")
def cleanup():
    """Terminate only mongods whose executable is inside .embedmongo."""
    processes = embedded_mongo_processes()
    if not processes:
        console.print("No embedded Mongo test processes are running.")
        return
    for process in processes:
        console.print(f"Stopping {process.describe()}", markup=False)
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        remaining = embedded_mongo_processes()
        if not remaining:
            console.print(f"Stopped {len(processes)} embedded Mongo test process(es).")
            return
        time.sleep(0.1)
    detail = ", ".join(process.describe() for process in remaining)
    console.print(f"Embedded Mongo process(es) did not stop: {detail}", markup=False)
    raise typer.Exit(1)

import os
import signal
import time

import typer
from rich.console import Console

from org.metadatacenter.smoke_gate import run_smoke
from org.metadatacenter.util.BuildSafety import embedded_mongo_processes


app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("e2e")
def e2e():
    """Run both whole-stack smoke tiers and record the evidence the train and release gates require."""
    raise typer.Exit(run_smoke())


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

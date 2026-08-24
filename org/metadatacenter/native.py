from typing import List, Optional

import typer

from org.metadatacenter.worker.NativeWorker import NativeWorker

app = typer.Typer(no_args_is_help=True)


@app.command("status")
def status():
    """Show PID, port, health, binary freshness, and log error counts."""
    NativeWorker.status()


@app.command("health")
def health():
    """Exit successfully only when every managed native application is healthy."""
    result = NativeWorker.health()
    if result.returncode:
        raise typer.Exit(result.returncode)


@app.command("watch")
def watch():
    """Continuously refresh native application status."""
    NativeWorker.watch()


@app.command("restart")
def restart(services: Optional[List[str]] = typer.Argument(None)):
    """Restart all managed applications, or only the named applications."""
    NativeWorker.restart(services or ())


@app.command("logs")
def logs(service: str = typer.Argument(...)):
    """Follow the log for one managed native application."""
    NativeWorker.logs(service)

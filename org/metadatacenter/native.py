from typing import List, Optional

import typer
from rich.console import Console

from org.metadatacenter import start, stop
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.NativeWorker import NativeWorker

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def require_native_mode(ctx: typer.Context):
    try:
        # Stop remains available when stale Docker state makes the selected native topology
        # inconsistent. The hardened native controller only terminates verified CEDAR processes.
        ModeManager.require_surface(
            "native", check_runtime=ctx.invoked_subcommand != "stop")
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


app.add_typer(start.app, name="start", help="Start native CEDAR components.")
app.add_typer(stop.app, name="stop", help="Stop native CEDAR components.")


@app.command("status")
def status():
    """Show native process health and expected host-port availability."""
    _require_native_backend("status")
    exit_on_failure(NativeWorker.status())


@app.command("health")
def health():
    """Exit successfully only when every managed native application is healthy."""
    _require_native_backend("health")
    result = NativeWorker.health()
    if result.returncode:
        raise typer.Exit(result.returncode)


@app.command("watch")
def watch():
    """Continuously refresh native application status."""
    _require_native_backend("watch")
    exit_on_failure(NativeWorker.watch())


@app.command("restart")
def restart(services: Optional[List[str]] = typer.Argument(None)):
    """Restart all managed applications, or only the named applications."""
    requested = services or ()
    _require_known_native_services(requested, "restart")
    mode = ModeManager.require_surface("native")
    if mode is CedarMode.HYBRID:
        _require_native_frontend_services(requested, "restart")
    exit_on_failure(NativeWorker.restart(requested))


@app.command("logs")
def logs(service: str = typer.Argument(...),
         lines: int = typer.Option(
             100, "-n", "--lines",
             help="Lines of history to show before following."),
         dropwizard: bool = typer.Option(
             False, "--dropwizard",
             help="Follow the service's Dropwizard appender log, which survives restarts and "
                  "rotates daily, rather than its standard output, which start truncates.")):
    """Follow the log for one managed native application."""
    mode = ModeManager.require_surface("native")
    if mode is CedarMode.HYBRID:
        _require_native_frontend_services((service,), "logs")
    # Replaces this process with tail, so nothing after it runs.
    NativeWorker.logs(service, lines, dropwizard)


# One log is being followed, so the singular is what a caller reaches for first.
app.command("log", hidden=True)(logs)


def _require_native_backend(operation):
    try:
        ModeManager.require_native_backend(operation)
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


def _require_known_native_services(services, operation):
    """Reject a name no native application answers to.

    The controller takes service names verbatim, so an unknown one reaches it as a service whose
    jar was never built, and the operator is told to build something that cannot exist. Unlike
    start and stop, which name each service in their own command, restart takes free text, so the
    check belongs here.
    """
    known = set(NativeWorker.MICROSERVICES) | set(NativeWorker.FRONTENDS)
    unknown = [service for service in services if service not in known]
    if not unknown:
        return
    console.print(
        f"[red]Not a native CEDAR application, so nothing to {operation}: "
        f"{', '.join(unknown)}[/red]")
    console.print(f"Known applications: {', '.join(sorted(known))}")
    raise typer.Exit(code=1)


def _require_native_frontend_services(services, operation):
    try:
        ModeManager.require_native_frontend_services(services, operation)
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)

from enum import Enum
from typing import Optional

import typer
from rich.console import Console

from org.metadatacenter.util.ModeManager import ModeError
from org.metadatacenter.worker.EnvWorker import EnvWorker

app = typer.Typer(no_args_is_help=True)
console = Console()


class EnvironmentSurface(str, Enum):
    NATIVE = "native"
    DOCKER = "docker"


def run(operation, *args):
    try:
        operation(*args)
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


@app.command("status", help="Show the selected mode and its effective environment sources")
def status():
    run(EnvWorker.status)


@app.command("list", help="List effective CEDAR variables with sensitive values redacted")
def env_list(surface: Optional[EnvironmentSurface] = typer.Argument(
        None, help="Environment surface to inspect in hybrid mode: native or docker")):
    run(EnvWorker.list, surface.value if surface else None)


@app.command("filter", help="Filter effective CEDAR variables with sensitive values redacted")
def filter(
        filter_term: str = typer.Argument(..., help="Environment variable name to search for"),
        surface: Optional[EnvironmentSurface] = typer.Argument(
            None, help="Environment surface to inspect in hybrid mode: native or docker"),
):
    run(EnvWorker.filter, filter_term, surface.value if surface else None)

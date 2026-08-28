from typing import Optional

import typer
from rich.console import Console

from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.ModeManager import ModeError, ModeManager

console = Console()


def mode(
        selected: Optional[CedarMode] = typer.Argument(
            None,
            help="Deployment mode to configure: native, hybrid, or docker.",
        ),
        clear: bool = typer.Option(
            False,
            "--clear",
            help="Clear the configured mode after its managed services have been stopped.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="With --clear, discard Docker state when the Docker daemon is unavailable.",
        ),
):
    """Show, configure, or clear the persistent CEDAR deployment mode."""
    try:
        if clear:
            if selected is not None:
                raise ModeError("Use a mode value or --clear, not both")
            previous = ModeManager.require_mode()
            discarded = ModeManager.clear(force=force)
            detail = "; inactive Docker deployment record discarded" if discarded else ""
            console.print(f"CEDAR mode cleared (was {previous.value}{detail}).")
            return

        if force:
            raise ModeError("--force is valid only with --clear")

        if selected is None:
            current = ModeManager.current()
            if current is None:
                console.print("CEDAR mode is not set.")
            else:
                console.print(f"CEDAR mode: {current.value}")
            return

        ModeManager.configure(selected)
        console.print(f"CEDAR mode configured: {selected.value}")
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)

import typer
from rich.console import Console

from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.ProdWorker import ProdError, ProdWorker

app = typer.Typer(no_args_is_help=True)
console = Console()


def run_prod_action(action):
    try:
        exit_on_failure(action())
    except (ProdError, KeyError, OSError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


@app.command("configure-frontends")
def configure_frontends():
    """Configure built static frontends for the active production domain."""
    run_prod_action(ProdWorker.configure_frontends)


@app.command("reset-frontends")
def reset_frontends():
    """Restore tracked static frontend entry points from Git."""
    run_prod_action(ProdWorker.reset_frontends)

import typer
from rich.console import Console

from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.DevWorker import DevError, DevWorker

console = Console()

app = typer.Typer(no_args_is_help=True)


def run_dev_action(action):
    try:
        exit_on_failure(action())
    except (DevError, KeyError, OSError, ValueError) as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


@app.command("create-directories")
def create_directories():
    """Create the local log, cache, export, certificate, and temporary directories."""
    run_dev_action(DevWorker.create_directories)


@app.command("add-hosts")
def add_hosts():
    """Add missing CEDAR development hostnames to /etc/hosts."""
    run_dev_action(DevWorker.add_hosts)


@app.command("copy-keycloak-listener")
def copy_keycloak_listener():
    """Install the built CEDAR event listener into the configured Keycloak."""
    run_dev_action(DevWorker.copy_keycloak_listener)


@app.command("generate-api-key")
def generate_api_key(user_id: str = typer.Argument('', help="User id")):
    """Generate the deterministic CEDAR API key for a user identifier."""
    run_dev_action(lambda: DevWorker.generate_api_key(user_id))

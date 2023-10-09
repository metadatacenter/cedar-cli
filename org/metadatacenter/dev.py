import typer
from rich.console import Console

from org.metadatacenter.worker.DevWorker import DevWorker

console = Console()

app = typer.Typer(no_args_is_help=True)


@app.command("create-directories")
def create_directories():
    DevWorker.create_directories()


@app.command("add-hosts")
def add_hosts():
    DevWorker.add_hosts()


@app.command("copy-keycloak-listener")
def add_hosts():
    DevWorker.copy_keycloak_listener()


@app.command("generate-api-key")
def generate_api_key(user_id: str = typer.Argument('', help="User id")):
    DevWorker.generate_api_key(user_id)

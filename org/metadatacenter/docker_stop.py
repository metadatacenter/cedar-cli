import typer
from rich.console import Console

from org.metadatacenter.model.DockerComponentTarget import (
    DockerFrontendTarget,
    DockerMicroserviceTarget,
)
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def require_docker_mode():
    try:
        ModeManager.require_surface("docker")
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


def exit_on_failure(returncode):
    if returncode:
        raise typer.Exit(code=returncode)


@app.command("all", help="Stop the active Docker deployment without removing named data volumes.")
def stop_all():
    exit_on_failure(DockerWorker.stop_all(ModeManager.docker_topology()))


@app.command("infra", help="Stop the infrastructure Compose project.")
def stop_infrastructure():
    exit_on_failure(DockerWorker.stop_infrastructure())


@app.command("keycloak", help="Stop the Keycloak container.")
@app.command("kk", help="Stop the Keycloak container.")
def stop_keycloak():
    exit_on_failure(DockerWorker.stop_keycloak())


@app.command("microservices", help="Stop the microservice Compose project.")
def stop_microservices():
    exit_on_failure(DockerWorker.stop_microservices())


@app.command("microservice", help="Stop one Java microservice, or all microservices.")
def stop_microservice(microservice: DockerMicroserviceTarget = typer.Argument(...)):
    exit_on_failure(DockerWorker.stop_microservice(microservice.value))


@app.command("frontends", help="Stop the seven-frontend Compose project.")
def stop_frontends():
    try:
        ModeManager.require_docker_frontends("stop")
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)
    exit_on_failure(DockerWorker.stop_frontends())


@app.command("frontend", help="Stop one frontend container, or all frontend containers.")
def stop_frontend(frontend: DockerFrontendTarget = typer.Argument(...)):
    try:
        ModeManager.require_docker_frontends("stop")
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)
    exit_on_failure(DockerWorker.stop_frontend(frontend.value))


@app.command("admin", help="Stop the optional admin-tool Compose project.")
def stop_admin():
    exit_on_failure(DockerWorker.stop_admin())

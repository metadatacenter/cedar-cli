import typer

from org.metadatacenter.model.DockerComponentTarget import (
    DockerFrontendTarget,
    DockerMicroserviceTarget,
)
from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)


def exit_on_failure(returncode):
    if returncode:
        raise typer.Exit(code=returncode)


@app.command("all", help="Stop the active Docker deployment without removing named data volumes.")
def stop_all():
    exit_on_failure(DockerWorker.stop_all())


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
    exit_on_failure(DockerWorker.stop_frontends())


@app.command("frontend", help="Stop one frontend container, or all frontend containers.")
def stop_frontend(frontend: DockerFrontendTarget = typer.Argument(...)):
    exit_on_failure(DockerWorker.stop_frontend(frontend.value))


@app.command("admin", help="Stop the optional admin-tool Compose project.")
def stop_admin():
    exit_on_failure(DockerWorker.stop_admin())

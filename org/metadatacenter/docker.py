from typing import Optional

import typer

from org.metadatacenter import docker_build, docker_remove, docker_start, docker_stop
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)
app.command("build")(docker_build.build)
app.add_typer(docker_remove.app, name="remove", help="Remove Docker containers, images, volumes, or network.")
app.add_typer(
    docker_start.app,
    name="start",
    help="Start a complete deployment, Compose project, or individual component.",
)
app.add_typer(
    docker_stop.app,
    name="stop",
    help="Stop a complete deployment, Compose project, or individual component.",
)


def exit_on_failure(returncode):
    if returncode:
        raise typer.Exit(code=returncode)


@app.command("status")
def status(
        mode: Optional[DockerDeploymentMode] = typer.Option(
            None,
            "--mode",
            help="Expected topology. Defaults to the last successful aggregate deployment, then full.",
        )):
    """Check container health and the acceptance checks for the active Docker mode."""
    if mode is None:
        mode = DockerWorker.active_deployment() or DockerDeploymentMode.FULL
    if not DockerWorker.status(mode=mode):
        raise typer.Exit(code=1)


@app.command("validate")
def validate():
    """Check every compose stack parses and every variable it references is defined. Needs no daemon."""
    exit_on_failure(DockerWorker.validate())


@app.command("create-network", help="Recreate the external cedarnet network from the active profile.")
def create_network():
    exit_on_failure(DockerWorker.create_network())


@app.command("create-certificates-volume", help="Create the external cedar_cert and cedar_ca volumes.")
def create_certificates_volume():
    exit_on_failure(DockerWorker.create_certificates_volume())


@app.command("copy-certificates", help="Copy configured or bundled certificates into Docker volumes.")
def copy_certificates():
    exit_on_failure(DockerWorker.copy_certificates())


@app.command("one-time-setup", help="Recreate cedarnet, create certificate volumes, and populate them.")
def one_time_setup():
    exit_on_failure(DockerWorker.create_network())
    exit_on_failure(DockerWorker.create_certificates_volume())
    exit_on_failure(DockerWorker.copy_certificates())

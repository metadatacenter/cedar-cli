from enum import Enum

import typer
from rich.console import Console

from org.metadatacenter.model.DockerComponentTarget import (
    DockerFrontendTarget,
    DockerMicroserviceTarget,
)
from org.metadatacenter.util.BuildTrain import DockerTrain
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)
console = Console()

DETACH = typer.Option(False, "--detach", help="Run in the background and return.")


class PullPolicy(str, Enum):
    always = "always"
    missing = "missing"
    never = "never"


PULL = typer.Option(
    PullPolicy.never,
    "--pull",
    help="Image pull policy. Local snapshot deployments default to never.",
)

TIMEOUT = typer.Option(
    600,
    "--timeout",
    min=1,
    help="Maximum seconds to wait for the selected deployment to become ready.",
)

TRAIN = typer.Option(
    None,
    "--train",
    help="Use this completed immutable train instead of the current completed train.",
)

LOCAL = typer.Option(
    False,
    "--local",
    help="Use the legacy development tag from the Docker manifests instead of a published train.",
)


def exit_on_failure(returncode):
    if returncode:
        raise typer.Exit(code=returncode)


@app.callback()
def require_docker_mode():
    try:
        mode = ModeManager.require_surface("docker")
        ModeManager.require_docker_start_compatible(mode)
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


def resolve_train(train, local, prefer_active=False):
    if train and local:
        raise typer.BadParameter("use --local or --train, not both")
    if local:
        return None
    if prefer_active and not train:
        active_mode = DockerWorker.active_deployment()
        if active_mode is not None:
            return DockerWorker.active_train()
    try:
        return DockerTrain.resolve(train)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


@app.command("all", help="Start the selected CEDAR deployment in dependency order and wait for readiness.")
def start_all(
        pull: PullPolicy = PULL,
        timeout: int = TIMEOUT,
        train: str = TRAIN,
        local: bool = LOCAL):
    mode = ModeManager.docker_topology()
    exit_on_failure(DockerWorker.start_all(
        mode=mode,
        pull=pull.value,
        timeout=timeout,
        train=resolve_train(train, local),
    ))


@app.command("infra", help="Start the seven infrastructure containers.")
def start_infrastructure(detach: bool = DETACH, pull: PullPolicy = PULL,
                         train: str = TRAIN, local: bool = LOCAL):
    exit_on_failure(DockerWorker.start_infrastructure(
        detach, pull.value, resolve_train(train, local, prefer_active=True)))


@app.command("keycloak", help="Start the Keycloak container.")
@app.command("kk", help="Start the Keycloak container.")
def start_keycloak(detach: bool = DETACH, pull: PullPolicy = PULL,
                   train: str = TRAIN, local: bool = LOCAL):
    exit_on_failure(DockerWorker.start_keycloak(
        detach, pull.value, resolve_train(train, local, prefer_active=True)))


@app.command("microservices", help="Start the fifteen Java microservices.")
def start_microservices(detach: bool = DETACH, pull: PullPolicy = PULL,
                        train: str = TRAIN, local: bool = LOCAL):
    exit_on_failure(DockerWorker.start_microservices(
        detach, pull.value, resolve_train(train, local, prefer_active=True)))


@app.command("microservice", help="Start one Java microservice, or all microservices.")
def start_microservice(
        microservice: DockerMicroserviceTarget = typer.Argument(...),
        detach: bool = DETACH,
        pull: PullPolicy = PULL,
        train: str = TRAIN,
        local: bool = LOCAL):
    exit_on_failure(DockerWorker.start_microservice(
        microservice.value,
        detach,
        pull.value,
        resolve_train(train, local, prefer_active=True),
    ))


@app.command("frontends", help="Start all seven frontend containers.")
def start_frontends(detach: bool = DETACH, pull: PullPolicy = PULL,
                    train: str = TRAIN, local: bool = LOCAL):
    try:
        ModeManager.require_docker_frontends("start")
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)
    exit_on_failure(DockerWorker.start_frontends(
        detach, pull.value, resolve_train(train, local, prefer_active=True)))


@app.command("frontend", help="Start one frontend container, or all frontend containers.")
def start_frontend(
        frontend: DockerFrontendTarget = typer.Argument(...),
        detach: bool = DETACH,
        pull: PullPolicy = PULL,
        train: str = TRAIN,
        local: bool = LOCAL):
    try:
        ModeManager.require_docker_frontends("start")
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)
    exit_on_failure(DockerWorker.start_frontend(
        frontend.value,
        detach,
        pull.value,
        resolve_train(train, local, prefer_active=True),
    ))


@app.command("admin", help="Start the four optional admin-tool containers.")
def start_admin(detach: bool = DETACH, pull: PullPolicy = PULL,
                train: str = TRAIN, local: bool = LOCAL):
    exit_on_failure(DockerWorker.start_admin(
        detach, pull.value, resolve_train(train, local, prefer_active=True)))

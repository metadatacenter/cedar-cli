from enum import Enum

import typer

from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)

DETACH = typer.Option(False, "--detach", "-d", help="Run in the background and return.")


class PullPolicy(str, Enum):
    always = "always"
    missing = "missing"
    never = "never"


PULL = typer.Option(
    PullPolicy.never,
    "--pull",
    help="Image pull policy. Local snapshot deployments default to never.",
)


def exit_on_failure(returncode):
    if returncode:
        raise typer.Exit(code=returncode)


@app.command("infrastructure", help="Start the seven infrastructure containers.")
def start_infrastructure(detach: bool = DETACH, pull: PullPolicy = PULL):
    exit_on_failure(DockerWorker.start_infrastructure(detach, pull.value))


@app.command("microservices", help="Start the fifteen Java microservices.")
def start_microservices(detach: bool = DETACH, pull: PullPolicy = PULL):
    exit_on_failure(DockerWorker.start_microservices(detach, pull.value))


@app.command("frontends", help="Start all seven frontend containers.")
def start_frontends(detach: bool = DETACH, pull: PullPolicy = PULL):
    exit_on_failure(DockerWorker.start_frontends(detach, pull.value))


@app.command("admin", help="Start the four optional admin-tool containers.")
def start_admin(detach: bool = DETACH, pull: PullPolicy = PULL):
    exit_on_failure(DockerWorker.start_admin(detach, pull.value))

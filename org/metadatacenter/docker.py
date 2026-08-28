import typer
from rich.console import Console

from org.metadatacenter import docker_build, docker_remove, docker_setup, docker_start, docker_stop
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def require_docker_mode(ctx: typer.Context):
    # Building an image is independent of the selected runtime topology. In particular, immutable
    # train jobs run in a clean workspace with no deployment profile or persistent mode. Runtime,
    # setup, validation, and removal commands remain behind the normal Docker-mode safety gate.
    if ctx.invoked_subcommand == "build":
        return
    try:
        # Cleanup must remain available when the recorded deployment and selected mode disagree.
        ModeManager.require_surface(
            "docker", check_runtime=ctx.invoked_subcommand != "stop")
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


app.command("build")(docker_build.build)
app.add_typer(docker_remove.app, name="remove", help="Remove Docker containers, images, volumes, or network.")
app.add_typer(
    docker_setup.app,
    name="setup",
    help="Prepare the Docker network and certificate volumes.",
)
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
def status():
    """Check container health and acceptance for the configured CEDAR mode."""
    mode = ModeManager.docker_topology()
    if not DockerWorker.status(mode=mode):
        raise typer.Exit(code=1)


@app.command("validate")
def validate():
    """Check every compose stack parses and every variable it references is defined. Needs no daemon."""
    exit_on_failure(DockerWorker.validate())

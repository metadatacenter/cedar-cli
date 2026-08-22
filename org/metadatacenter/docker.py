import typer

from org.metadatacenter import docker_build, docker_remove, docker_start, docker_stop
from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)
app.command("build")(docker_build.build)
app.add_typer(docker_remove.app, name="remove")
app.add_typer(docker_start.app, name="start")
app.add_typer(docker_stop.app, name="stop")


@app.command("status")
def status(
        include_frontends: bool = typer.Option(
            True,
            "--frontends/--no-frontends",
            help="Require the frontend containers; disable for a Docker-backend/native-frontend hybrid.",
        ),
        include_admin: bool = typer.Option(
            False,
            "--include-admin",
            help="Also require the optional admin-tool containers.",
        )):
    """Check expected Compose services against Docker runtime health."""
    if not DockerWorker.status(include_frontends=include_frontends, include_admin=include_admin):
        raise typer.Exit(code=1)


@app.command("validate")
def validate():
    """Check every compose stack parses and every variable it references is defined. Needs no daemon."""
    DockerWorker.validate()


@app.command("create-network")
def create_network():
    DockerWorker.create_network()


@app.command("create-certificates-volume")
def create_certificates_volume():
    DockerWorker.create_certificates_volume()


@app.command("copy-certificates")
def copy_certificates():
    DockerWorker.copy_certificates()


@app.command("one-time-setup")
def one_time_setup():
    DockerWorker.create_network()
    DockerWorker.create_certificates_volume()
    DockerWorker.copy_certificates()

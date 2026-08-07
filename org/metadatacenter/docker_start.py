import typer

from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)

DETACH = typer.Option(False, "--detach", "-d", help="Run in the background and return.")


@app.command("infrastructure")
def start_infrastructure(detach: bool = DETACH):
    DockerWorker.start_infrastructure(detach)


@app.command("microservices")
def start_microservices(detach: bool = DETACH):
    DockerWorker.start_microservices(detach)


@app.command("frontends")
def start_frontends(detach: bool = DETACH):
    DockerWorker.start_frontends(detach)


@app.command("admin")
def start_admin(detach: bool = DETACH):
    DockerWorker.start_admin(detach)

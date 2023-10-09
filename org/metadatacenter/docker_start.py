import typer

from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)


@app.command("infrastructure")
def start_infrastructure():
    DockerWorker.start_infrastructure()


@app.command("microservices")
def start_microservices():
    DockerWorker.start_microservices()


@app.command("frontends")
def start_frontends():
    DockerWorker.start_frontends()

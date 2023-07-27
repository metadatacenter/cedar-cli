import typer

from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)


@app.command("infrastructure")
def stop_infrastructure():
    DockerWorker.stop_infrastructure()


@app.command("microservices")
def stop_microservices():
    DockerWorker.stop_microservices()


@app.command("frontends")
def stop_frontends():
    DockerWorker.stop_frontends()

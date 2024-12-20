import typer

from org.metadatacenter.worker.StopMicroserviceWorker import StopMicroserviceWorker

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def microservice_all():
    StopMicroserviceWorker.all()


@app.command("bridge")
def microservice_bridge():
    StopMicroserviceWorker.bridge()

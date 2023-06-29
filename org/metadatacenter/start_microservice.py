import typer

from org.metadatacenter.worker.StartMicroserviceWorker import StartMicroserviceWorker

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def microservice_all():
    StartMicroserviceWorker.all()

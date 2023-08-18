import typer

from org.metadatacenter import stop_frontend, stop_microservice
from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker
from org.metadatacenter.worker.StopInfrastructureWorker import StopInfrastructureWorker
from org.metadatacenter.worker.StopMicroserviceWorker import StopMicroserviceWorker

app = typer.Typer(no_args_is_help=True)
app.add_typer(stop_frontend.app, name="frontend")
app.add_typer(stop_microservice.app, name="microservice")


@app.command("all")
def all_all():
    StopInfrastructureWorker.all()
    StopMicroserviceWorker.all()
    StopFrontendWorker.all()


@app.command("infra")
def all_all():
    StopInfrastructureWorker.all()

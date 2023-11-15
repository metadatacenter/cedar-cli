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
    StopFrontendWorker.all()
    StopMicroserviceWorker.all()
    StopInfrastructureWorker.all()


@app.command("infra")
def infra_all():
    StopInfrastructureWorker.all()


@app.command("microservices")
def microservice_all():
    StopMicroserviceWorker.all()


@app.command("frontends")
def frontend_all():
    StopFrontendWorker.all()

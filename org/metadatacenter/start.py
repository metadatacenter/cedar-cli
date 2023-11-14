import typer

from org.metadatacenter import start_frontend, start_microservice
from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker
from org.metadatacenter.worker.StartInfrastructureWorker import StartInfrastructureWorker
from org.metadatacenter.worker.StartMicroserviceWorker import StartMicroserviceWorker

app = typer.Typer(no_args_is_help=True)
app.add_typer(start_frontend.app, name="frontend")
app.add_typer(start_microservice.app, name="microservice")


@app.command("all")
def all_all():
    StartInfrastructureWorker.all()
    StartMicroserviceWorker.all()
    StartFrontendWorker.all()


@app.command("infra")
def infra_all():
    StartInfrastructureWorker.all()


@app.command("microservices")
def microservice_all():
    StartMicroserviceWorker.all()


@app.command("frontends")
def frontend_all():
    StartFrontendWorker.all()

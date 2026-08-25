import typer
from rich.console import Console

from org.metadatacenter import stop_frontend, stop_microservice
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker
from org.metadatacenter.worker.StopInfrastructureWorker import StopInfrastructureWorker
from org.metadatacenter.worker.StopMicroserviceWorker import StopMicroserviceWorker
from org.metadatacenter.worker.NativeWorker import NativeWorker

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def require_allowed_native_stop(ctx: typer.Context):
    try:
        mode = ModeManager.require_surface("native")
        if mode is CedarMode.HYBRID and ctx.invoked_subcommand not in ("frontends", "frontend"):
            raise ModeError(
                f"CEDAR mode is hybrid; native stop {ctx.invoked_subcommand} would operate on the Docker backend"
            )
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


app.add_typer(stop_frontend.app, name="frontend")
app.add_typer(stop_microservice.app, name="microservice")


@app.command("all")
def all_all():
    exit_on_failure(NativeWorker.stop())
    exit_on_failure(StopInfrastructureWorker.all())


@app.command("backends")
def backend_all():
    exit_on_failure(StopMicroserviceWorker.all())
    exit_on_failure(StopInfrastructureWorker.all())


@app.command("infra")
def infra_all():
    exit_on_failure(StopInfrastructureWorker.all())


@app.command("microservices")
def microservice_all():
    exit_on_failure(StopMicroserviceWorker.all())


@app.command("frontends")
def frontend_all():
    exit_on_failure(StopFrontendWorker.all())


@app.command("kk")
def infra_kk():
    exit_on_failure(StopInfrastructureWorker.keycloak())


@app.command("keycloak")
def infra_keycloak():
    infra_kk()

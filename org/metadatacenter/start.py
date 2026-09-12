import time

import typer
from rich.console import Console

from org.metadatacenter import start_frontend, start_microservice
from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker
from org.metadatacenter.worker.StartInfrastructureWorker import StartInfrastructureWorker
from org.metadatacenter.worker.StartMicroserviceWorker import StartMicroserviceWorker
from org.metadatacenter.worker.NativeWorker import NativeWorker

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.callback()
def require_allowed_native_start(ctx: typer.Context):
    try:
        mode = ModeManager.require_surface("native")
        if mode is CedarMode.HYBRID and ctx.invoked_subcommand not in ("frontends", "frontend"):
            raise ModeError(
                f"CEDAR mode is hybrid; native start {ctx.invoked_subcommand} would operate on the Docker backend"
            )
    except ModeError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(code=1)


app.add_typer(start_frontend.app, name="frontend")
app.add_typer(start_microservice.app, name="microservice")


def _start_infrastructure_once():
    """Start the infrastructure the host is missing, and say so when it is missing none.

    Starting infrastructure that already runs is not harmless. Keycloak refuses a bound 8080
    and returns non-zero, which used to end `start all` before it reached the applications it
    was asked for. An operator whose stack is up and who wants fresh binaries wants
    `cedarcli native restart`; saying that here is cheaper than leaving them to read a
    Quarkus port-binding error and guess.
    """
    gaps = NativeWorker.infrastructure_gaps()
    if gaps == []:
        console.print(
            "Infrastructure is already listening on every managed port. Starting applications "
            "only; `cedarcli native restart` redeploys them from current binaries.")
        return
    if gaps:
        console.print(f"Starting infrastructure; {', '.join(gaps)} not listening.")
    exit_on_failure(StartInfrastructureWorker.all())
    _await_infrastructure()


def _await_infrastructure(timeout_seconds: int = 180, interval_seconds: int = 2):
    """Return once every managed infrastructure port is served, or say which are not.

    A microservice reaches Neo4j, Mongo and Keycloak while it boots, so returning from here
    early is what produces the failure a readiness check on the applications can only report
    afterwards. Waiting here prevents the cascade instead.

    A controller that cannot say which ports are expected returns None, and there is nothing
    to wait for in that case.
    """
    deadline = time.monotonic() + timeout_seconds
    gaps = NativeWorker.infrastructure_gaps()
    while gaps:
        if time.monotonic() >= deadline:
            console.print(
                f"[red]Infrastructure did not come up within {timeout_seconds}s: "
                f"{', '.join(gaps)} still not listening.[/red]")
            raise typer.Exit(code=1)
        time.sleep(interval_seconds)
        gaps = NativeWorker.infrastructure_gaps()
    if gaps == []:
        console.print("Infrastructure is listening on every managed port.")


@app.command("all")
def all_all():
    _start_infrastructure_once()
    exit_on_failure(NativeWorker.start())


@app.command("backends")
def backend_all():
    _start_infrastructure_once()
    exit_on_failure(StartMicroserviceWorker.all())


@app.command("infra")
def infra_all():
    _start_infrastructure_once()


@app.command("microservices")
def microservice_all():
    exit_on_failure(StartMicroserviceWorker.all())


@app.command("frontends")
def frontend_all():
    exit_on_failure(StartFrontendWorker.all())


@app.command("kk")
def infra_kk():
    exit_on_failure(StartInfrastructureWorker.keycloak())


@app.command("keycloak")
def infra_keycloak():
    infra_kk()

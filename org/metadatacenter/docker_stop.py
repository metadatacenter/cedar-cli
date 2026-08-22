import typer

from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)


def exit_on_failure(returncode):
    if returncode:
        raise typer.Exit(code=returncode)


@app.command("infrastructure", help="Stop the infrastructure Compose project.")
def stop_infrastructure():
    exit_on_failure(DockerWorker.stop_infrastructure())


@app.command("microservices", help="Stop the microservice Compose project.")
def stop_microservices():
    exit_on_failure(DockerWorker.stop_microservices())


@app.command("frontends", help="Stop the seven-frontend Compose project.")
def stop_frontends():
    exit_on_failure(DockerWorker.stop_frontends())


@app.command("admin", help="Stop the optional admin-tool Compose project.")
def stop_admin():
    exit_on_failure(DockerWorker.stop_admin())

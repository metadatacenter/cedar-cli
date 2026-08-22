import typer

from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)


def exit_on_failure(returncode):
    if returncode:
        raise typer.Exit(code=returncode)


@app.command("containers", help="Force-remove containers built from CEDAR images.")
def remove_containers():
    exit_on_failure(DockerWorker.remove_containers())


@app.command("images", help="Remove local metadatacenter/cedar-* images.")
def remove_images():
    exit_on_failure(DockerWorker.remove_images())


@app.command("network", help="Remove cedarnet if it exists and is unused.")
def remove_network():
    exit_on_failure(DockerWorker.remove_network())


@app.command("volumes", help="Delete all CEDAR data, state, certificate, and log volumes.")
def remove_volumes():
    exit_on_failure(DockerWorker.remove_volumes())


@app.command("all", help="Remove CEDAR containers, images, volumes, and cedarnet.")
def remove_all():
    returncodes = [
        DockerWorker.remove_containers(),
        DockerWorker.remove_images(),
        DockerWorker.remove_volumes(),
        DockerWorker.remove_network(),
    ]
    exit_on_failure(next((code for code in returncodes if code), 0))

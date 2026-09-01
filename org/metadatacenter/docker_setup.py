import typer

from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)


def exit_on_failure(returncode):
    if returncode:
        raise typer.Exit(code=returncode)


@app.command("one-time-setup", help="Recreate cedarnet, generate missing certificates, and populate their volumes.")
def one_time_setup():
    """Perform every Docker host bootstrap operation in dependency order."""
    exit_on_failure(DockerWorker.create_network())
    exit_on_failure(DockerWorker.create_certificates_volume())
    exit_on_failure(DockerWorker.copy_certificates())


@app.command("create-network", help="Recreate the external cedarnet network from the active profile.")
def create_network():
    exit_on_failure(DockerWorker.create_network())


@app.command("create-certificates-volume", help="Create the external cedar_cert and cedar_ca volumes.")
def create_certificates_volume():
    exit_on_failure(DockerWorker.create_certificates_volume())


@app.command("copy-certificates", help="Generate missing local certificates and copy them into Docker volumes.")
def copy_certificates():
    exit_on_failure(DockerWorker.copy_certificates())

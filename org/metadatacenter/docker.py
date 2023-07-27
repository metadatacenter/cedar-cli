import typer

from org.metadatacenter import docker_remove, docker_start, docker_stop
from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)
app.add_typer(docker_remove.app, name="remove")
app.add_typer(docker_start.app, name="start")
app.add_typer(docker_stop.app, name="stop")


@app.command("create-network")
def create_network():
    DockerWorker.create_network()


@app.command("create-certificates-volume")
def create_certificates_volume():
    DockerWorker.create_certificates_volume()


@app.command("copy-certificates")
def copy_certificates():
    DockerWorker.copy_certificates()


@app.command("one-time-setup")
def one_time_setup():
    DockerWorker.create_network()
    DockerWorker.create_certificates_volume()
    DockerWorker.copy_certificates()

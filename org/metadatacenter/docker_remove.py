import typer

from org.metadatacenter.worker.DockerWorker import DockerWorker

app = typer.Typer(no_args_is_help=True)


@app.command("containers")
def remove_containers():
    DockerWorker.remove_containers()


@app.command("images")
def remove_images():
    DockerWorker.remove_images()


@app.command("network")
def remove_network():
    DockerWorker.remove_network()


@app.command("volumes")
def remove_volumes():
    DockerWorker.remove_volumes()


@app.command("all")
def remove_all():
    DockerWorker.remove_containers()
    DockerWorker.remove_images()
    DockerWorker.remove_volumes()
    DockerWorker.remove_network()

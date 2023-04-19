import typer

from org.metadatacenter.model.Repos import Repos
from org.metadatacenter.worker.EnvWorker import EnvWorker

app = typer.Typer()

repos = Repos()
env_worker = EnvWorker(repos)


@app.command("list")
def list():
    env_worker.list()


@app.command("core")
def core():
    env_worker.core()

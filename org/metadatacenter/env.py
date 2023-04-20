import typer

from org.metadatacenter.model.ReposFactory import ReposFactory
from org.metadatacenter.worker.EnvWorker import EnvWorker

app = typer.Typer()

repos = ReposFactory.build_repos()
env_worker = EnvWorker(repos)


@app.command("list")
def list():
    env_worker.list()


@app.command("core")
def core():
    env_worker.core()

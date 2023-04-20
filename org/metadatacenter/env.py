import typer

from org.metadatacenter.worker.EnvWorker import EnvWorker

app = typer.Typer()

env_worker = EnvWorker()


@app.command("list")
def list():
    env_worker.list()


@app.command("core")
def core():
    env_worker.core()

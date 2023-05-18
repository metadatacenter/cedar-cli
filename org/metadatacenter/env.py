import typer

from org.metadatacenter.worker.EnvWorker import EnvWorker

app = typer.Typer(no_args_is_help=True)

env_worker = EnvWorker()


@app.command("list")
def list():
    env_worker.list()


@app.command("core")
def core():
    env_worker.core()

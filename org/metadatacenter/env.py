import typer

from org.metadatacenter.worker.EnvWorker import EnvWorker

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def env_list():
    EnvWorker.list()


@app.command("core")
def core():
    EnvWorker.core()

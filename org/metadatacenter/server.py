import typer

from org.metadatacenter.worker.ServerWorker import ServerWorker

app = typer.Typer(no_args_is_help=True)


@app.command("status")
def status():
    ServerWorker.status()

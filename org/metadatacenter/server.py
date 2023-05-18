import typer

from org.metadatacenter.worker.ServerWorker import ServerWorker

app = typer.Typer(no_args_is_help=True)

server_worker = ServerWorker()


@app.command("status")
def status():
    print("Not implemented yet! 11")

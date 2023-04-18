import typer

from org.metadatacenter.ServerWorker import ServerWorker

app = typer.Typer()

server_worker = ServerWorker()


@app.command("status")
def status():
    print("Not implemented yet! 11")

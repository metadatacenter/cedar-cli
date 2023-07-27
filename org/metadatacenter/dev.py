import typer

from org.metadatacenter.worker.DevWorker import DevWorker

app = typer.Typer(no_args_is_help=True)


@app.command("create-directories")
def create_directories():
    DevWorker.create_directories()


@app.command("add-hosts")
def add_hosts():
    DevWorker.add_hosts()

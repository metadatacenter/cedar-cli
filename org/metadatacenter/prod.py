import typer

from org.metadatacenter.worker.ProdWorker import ProdWorker

app = typer.Typer(no_args_is_help=True)


@app.command("configure-frontends")
def configure_frontends():
    ProdWorker.configure_frontends()


@app.command("reset-frontends")
def reset_frontends():
    ProdWorker.reset_frontends()

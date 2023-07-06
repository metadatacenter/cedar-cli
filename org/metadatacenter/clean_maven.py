import typer

from org.metadatacenter.worker.CleanMavenWorker import CleanMavenWorker

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def clean_all():
    CleanMavenWorker.all()


@app.command("cedar")
def cedar():
    CleanMavenWorker.cedar()

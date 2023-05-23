import typer

from org.metadatacenter.worker.CleanMavenWorker import CleanMavenWorker

app = typer.Typer(no_args_is_help=True)
clean_maven_worker = CleanMavenWorker()


@app.command("all")
def clean_all():
    clean_maven_worker.all()


@app.command("cedar")
def cedar():
    clean_maven_worker.cedar()

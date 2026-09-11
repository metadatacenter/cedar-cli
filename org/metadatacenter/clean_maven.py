import typer
from org.metadatacenter.util.CliResult import exit_on_failure

from org.metadatacenter.worker.CleanMavenWorker import CleanMavenWorker

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def clean_all():
    exit_on_failure(CleanMavenWorker.all())


@app.command("cedar")
def cedar():
    exit_on_failure(CleanMavenWorker.cedar())

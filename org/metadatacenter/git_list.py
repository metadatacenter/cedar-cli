import typer

from org.metadatacenter.worker.GitWorker import GitWorker
from org.metadatacenter.util.CliResult import exit_on_failure

app = typer.Typer(no_args_is_help=True)

git_worker = GitWorker()


@app.command("branch")
def list_all():
    exit_on_failure(git_worker.list_branch())


@app.command("tag")
def docker():
    exit_on_failure(git_worker.list_tag())

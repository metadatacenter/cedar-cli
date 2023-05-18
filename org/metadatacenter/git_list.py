import typer

from org.metadatacenter.worker.GitWorker import GitWorker

app = typer.Typer(no_args_is_help=True)

git_worker = GitWorker()


@app.command("branch")
def all():
    git_worker.list_branch()


@app.command("tag")
def docker():
    git_worker.list_tag()

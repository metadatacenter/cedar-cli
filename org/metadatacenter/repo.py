import typer

from org.metadatacenter.worker.RepoWorker import RepoWorker

app = typer.Typer(no_args_is_help=True)

repo_worker = RepoWorker()


@app.command("list")
def repo_list():
    repo_worker.repo_list()


@app.command("status")
def repo_status():
    repo_worker.repo_status()


@app.command("report")
def repo_report():
    RepoWorker.repo_report()

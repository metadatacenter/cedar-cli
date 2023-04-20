import typer

from org.metadatacenter.model.ReposFactory import ReposFactory
from org.metadatacenter.worker.RepoWorker import RepoWorker

app = typer.Typer()

repos = ReposFactory.build_repos()
repo_worker = RepoWorker(repos)


@app.command("list")
def list_repos():
    repo_worker.list_repos()

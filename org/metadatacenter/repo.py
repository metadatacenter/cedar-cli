import typer

from org.metadatacenter.model.Repos import Repos
from org.metadatacenter.worker.RepoWorker import RepoWorker

app = typer.Typer()

repos = Repos()
repo_worker = RepoWorker(repos)


@app.command("list")
def list_repos():
    repo_worker.list_repos()

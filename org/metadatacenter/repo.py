import typer

from org.metadatacenter.worker.RepoWorker import RepoWorker

app = typer.Typer()

repo_worker = RepoWorker()


@app.command("list")
def list_repos():
    repo_worker.list_repos()

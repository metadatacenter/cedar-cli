import typer

from org.metadatacenter import git_clone
from org.metadatacenter.model.Repos import Repos
from org.metadatacenter.worker.GitWorker import GitWorker

app = typer.Typer()
app.add_typer(git_clone.app, name="clone")

repos = Repos()
git_worker = GitWorker(repos)


@app.command("status")
def status():
    git_worker.status()


@app.command("branch")
def branch():
    git_worker.branch()


@app.command("pull")
def pull():
    git_worker.pull()


@app.command("checkout")
def checkout(branch: str):
    git_worker.checkout(branch)

import typer

from org.metadatacenter.GitWorker import GitWorker
from org.metadatacenter.Repos import Repos

app = typer.Typer()

repos = Repos()
git_worker = GitWorker(repos)


@app.command("all")
def all():
    git_worker.clone_all()


@app.command("docker")
def docker():
    git_worker.clone_docker()

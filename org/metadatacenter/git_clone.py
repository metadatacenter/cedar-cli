import typer

from org.metadatacenter.model.ReposFactory import ReposFactory
from org.metadatacenter.worker.GitWorker import GitWorker

app = typer.Typer()

repos = ReposFactory.build_repos()
git_worker = GitWorker(repos)


@app.command("all")
def all():
    git_worker.clone_all()


@app.command("docker")
def docker():
    git_worker.clone_docker()

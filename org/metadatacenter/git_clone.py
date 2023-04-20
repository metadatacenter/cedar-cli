import typer

from org.metadatacenter.worker.GitWorker import GitWorker

app = typer.Typer()

git_worker = GitWorker()


@app.command("all")
def all():
    git_worker.clone_all()


@app.command("docker")
def docker():
    git_worker.clone_docker()

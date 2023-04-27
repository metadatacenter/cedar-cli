import typer

from org.metadatacenter import git_clone
from org.metadatacenter.worker.GitWorker import GitWorker

app = typer.Typer()
app.add_typer(git_clone.app, name="clone")

git_worker = GitWorker()


@app.command("status")
def status():
    git_worker.status()


@app.command("branch")
def branch():
    git_worker.branch()


@app.command("pull")
def pull():
    git_worker.pull()

@app.command("fetch")
def pull():
    git_worker.fetch()


@app.command("checkout")
def checkout(branch: str):
    git_worker.checkout(branch)


@app.command("next")
def next():
    git_worker.next()

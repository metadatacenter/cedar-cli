import typer

from org.metadatacenter import git_clone, git_list
from org.metadatacenter.worker.GitWorker import GitWorker

app = typer.Typer(no_args_is_help=True)
app.add_typer(git_clone.app, name="clone")
app.add_typer(git_list.app, name="list")

git_worker = GitWorker()


@app.command("status")
def status():
    git_worker.status()


@app.command("branch")
def git_1branch():
    git_worker.branch()


@app.command("pull")
def pull():
    git_worker.pull()


@app.command("fetch")
def pull():
    git_worker.fetch()


@app.command("remote")
def remote():
    git_worker.remote()


@app.command("checkout")
def checkout(branch: str):
    git_worker.checkout(branch)


@app.command("next")
def git_next():
    git_worker.next()


@app.command("add-commit-push")
def git_add_commit_push(comment: str):
    git_worker.git_add_commit_push(comment)

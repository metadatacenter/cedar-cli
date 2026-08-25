import typer

from org.metadatacenter.worker.RepoWorker import RepoWorker
from org.metadatacenter.worker.VersionWorker import VersionWorker

app = typer.Typer(no_args_is_help=True)

version_worker = VersionWorker()


@app.command("versions")
def versions():
    version_worker.check_versions()


@app.command("repos")
def repos():
    RepoWorker.check_repos()

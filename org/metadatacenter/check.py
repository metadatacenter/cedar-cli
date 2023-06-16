import typer

from org.metadatacenter.worker.ArtifactsWorker import ArtifactsWorker
from org.metadatacenter.worker.RepoWorker import RepoWorker
from org.metadatacenter.worker.VersionWorker import VersionWorker

app = typer.Typer(no_args_is_help=True)

version_worker = VersionWorker()
artifacts_worker = ArtifactsWorker()


@app.command("versions")
def versions():
    version_worker.check_versions()


@app.command("artifacts")
def versions():
    artifacts_worker.check_artifacts()


@app.command("repos")
def repos():
    RepoWorker.check_repos()

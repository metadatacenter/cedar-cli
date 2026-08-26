import typer

from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.RepoWorker import RepoWorker
from org.metadatacenter.worker.VersionWorker import VersionWorker

app = typer.Typer(no_args_is_help=True)

version_worker = VersionWorker()


@app.command("versions")
def versions():
    """Check version declarations across configured repositories."""
    exit_on_failure(version_worker.check_versions())


@app.command("repos")
def repos():
    """Check that configured repositories exist and list unmanaged Git clones."""
    exit_on_failure(RepoWorker.check_repos())

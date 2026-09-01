import typer

from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.RepoWorker import RepoWorker
from org.metadatacenter.worker.SnapshotWorker import DEFAULT_NEXUS, SnapshotWorker
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


@app.command("snapshots")
def snapshots(
        version: str = typer.Option(
            None, "--version",
            help="Snapshot version to ask Nexus for. Defaults to cedar-parent's own on develop."),
        grace_hours: float = typer.Option(
            None, "--grace-hours",
            help="How long a snapshot may lag its source before that counts as unpublished."),
        nexus: str = typer.Option(
            DEFAULT_NEXUS, "--nexus", help="Snapshot repository base URL.")):
    """Check that each repository's published snapshot was built from its current source."""
    exit_on_failure(SnapshotWorker.check_snapshots(
        version=version, grace_hours=grace_hours, nexus=nexus))

import typer

from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.BuildTrainWorker import BuildTrainWorker
from org.metadatacenter.worker.OpenApiWorker import OpenApiWorker
from org.metadatacenter.worker.RepoWorker import RepoWorker
from org.metadatacenter.worker.SnapshotWorker import DEFAULT_NEXUS, SnapshotWorker
from org.metadatacenter.worker.VersionWorker import VersionWorker

app = typer.Typer(no_args_is_help=True)

version_worker = VersionWorker()


@app.command("versions")
def versions(
        by_file: bool = typer.Option(
            False, "--by-file",
            help="One row per version-carrying file instead of one per repository."),
        strict: bool = typer.Option(
            False, "--strict",
            help="Also fail when a repository is behind its remote. For a gate, which judges this "
                 "workspace rather than the estate.")):
    """Check version declarations across configured repositories."""
    exit_on_failure(version_worker.check_versions(by_file=by_file, strict=strict))


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


@app.command("openapi")
def openapi(
        show_all: bool = typer.Option(
            False, "--all",
            help="List every document in the detail section, not only those with findings.")):
    """Check that every committed OpenAPI document describes what a generated client needs."""
    exit_on_failure(OpenApiWorker.check_openapi(show_all=show_all))


@app.command("main")
def main(
        show_all: bool = typer.Option(
            False, "--all",
            help="List every repository, not only those whose main is ahead of develop.")):
    """Check whether any repository's main carries commits develop does not."""
    exit_on_failure(BuildTrainWorker.report_main_ahead(show_all=show_all))


@app.command("ci")
def ci(
        show_all: bool = typer.Option(
            False, "--all",
            help="List every repository, not only those whose CI is not green.")):
    """Check GitHub CI at the exact develop commit of every repository a train would capture."""
    exit_on_failure(BuildTrainWorker.report_source_ci(show_all=show_all))

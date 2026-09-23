import typer

from org.metadatacenter.ci_env import check_ci_env
from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.BuildTrainWorker import BuildTrainWorker
from org.metadatacenter.worker.ComponentWorker import ComponentWorker
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


@app.command("ci-env")
def ci_env(
        apply: bool = typer.Option(
            False, "--apply",
            help="Rewrite the copies that have drifted, for review and one commit per repository.")):
    """Check that every Java repository's CI gives its tests the environment the code requires."""
    exit_on_failure(check_ci_env(apply=apply))


@app.command("components")
def components(
        strict: bool = typer.Option(
            False, "--strict",
            help="Also fail when a host sits behind a published component or serves a local "
                 "build. For a server payload, which serves whatever the lock resolves; the "
                 "release and train preflights ask this check without it."),
        show_all: bool = typer.Option(
            False, "--all",
            help="List every comparison, not only those with findings.")):
    """Check that every browser application serves the component sources beside it."""
    exit_on_failure(ComponentWorker.check_components(strict=strict, show_all=show_all))


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


@app.command("design-tokens")
def design_tokens(
        repo: list[str] = typer.Option(None, "--repo", help="Frontend repository; repeat to select several."),
        strict: bool = typer.Option(False, "--strict", help="Fail on new color/typography drift or missing baselines."),
        json_output: bool = typer.Option(False, "--json", help="Emit a machine-readable adoption report."),
        show_all: bool = typer.Option(False, "--all", help="Include existing findings."),
        init_baseline: bool = typer.Option(False, "--init-baseline", help="Create reviewed debt baselines once; never overwrite."),
        prune_baseline: bool = typer.Option(False, "--prune-baseline", help="Remove resolved debt without increasing allowances.")):
    """Report shared-style adoption, new drift and token dependency pins across frontends."""
    from org.metadatacenter.design_tokens import check_design_tokens
    exit_on_failure(check_design_tokens(
        repos=repo, strict=strict, json_output=json_output, show_all=show_all,
        init_baseline=init_baseline, prune_baseline=prune_baseline))


@app.command("artifact-versioning")
def artifact_versioning(
        apply: bool = typer.Option(False, "--apply", help="Repair unambiguous graph latest flags and enqueue reindexing.")):
    """Audit artifact history links, ordering, draft uniqueness and latest flags in the selected stack."""
    from org.metadatacenter.version_lifecycle import check_versioning
    exit_on_failure(check_versioning(apply=apply))


@app.command("stores")
def stores():
    """Check that each artifact collection carries the unique @id index the code relies on."""
    from org.metadatacenter.worker.StoreWorker import StoreWorker
    exit_on_failure(StoreWorker.check_stores())

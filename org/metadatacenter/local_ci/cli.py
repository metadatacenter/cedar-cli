"""`cedarcli ci`: run repositories' CI on this machine and record it as evidence."""
from __future__ import annotations

from pathlib import Path

import typer

from org.metadatacenter.local_ci.errors import LocalCIError

app = typer.Typer(help="Run repositories' own CI on this machine at exact commits, and record the "
                       "result as evidence a gate accepts in place of a green GitHub run.")


def _cedar_home() -> Path:
    from org.metadatacenter.util.InvocationContext import invocation_environment
    home = invocation_environment().get("CEDAR_HOME")
    if not home:
        typer.echo("CEDAR_HOME is not set")
        raise typer.Exit(1)
    return Path(home)


def _registry_repositories(cedar_home: Path) -> list[str]:
    from org.metadatacenter.local_ci.registry import load_registry
    return load_registry(cedar_home).repositories()


@app.command("run")
def run_command(
        repositories: list[str] = typer.Argument(None, help="Repositories to run, by checkout name"),
        every: bool = typer.Option(False, "--all", help="Run every repository the registry names"),
        revision: list[str] = typer.Option(
            None, "--revision", help="Run <repository>=<commit> instead of origin/develop"),
        jobs: int = typer.Option(2, "--jobs", min=1, max=8, help="Repositories run at the same time"),
        no_status: bool = typer.Option(
            False, "--no-status", help="Record locally without posting the commit status"),
):
    """Run each repository's push-to-develop CI at its exact commit and record the result.

    The jobs run from fresh clones of the exact commits, in an environment that carries nothing
    from this shell beyond the toolchain. A repository whose CI includes a job the registry does not
    cover is left to GitHub, and its reasons are listed.
    """
    from org.metadatacenter.local_ci.run import parse_revisions, run
    cedar_home = _cedar_home()
    try:
        names = list(repositories or [])
        if every:
            names = sorted(set(names) | set(_registry_repositories(cedar_home)))
        if not names:
            typer.echo("Name the repositories to run, or pass --all.")
            raise typer.Exit(1)
        outcomes = run(cedar_home, names, revisions=parse_revisions(revision), jobs=jobs,
                       post=not no_status, reporter=lambda line: typer.echo(f"  {line}"))
    except LocalCIError as error:
        typer.echo(f"Stopped: {error}")
        raise typer.Exit(1) from error
    worst = 0
    typer.echo("")
    for outcome in outcomes:
        typer.echo(f"{outcome.state.upper():9} {outcome.repository} {outcome.revision[:8]}  {outcome.detail}")
        if outcome.record_path:
            typer.echo(f"          record: {outcome.record_path}")
        if outcome.status:
            typer.echo(f"          status: {outcome.status.get('context')} = {outcome.status.get('state')}")
        for reason in outcome.reasons[:8]:
            typer.echo(f"          {reason}")
        if len(outcome.reasons) > 8:
            typer.echo(f"          … {len(outcome.reasons) - 8} more; see cedarcli ci coverage {outcome.repository}")
        if outcome.state in ("failed", "error", "uncovered"):
            worst = 1
    raise typer.Exit(worst)


@app.command("coverage")
def coverage_command(
        repositories: list[str] = typer.Argument(None, help="Repositories to check; default every one "
                                                          "the registry names"),
        revision: list[str] = typer.Option(None, "--revision", help="Check <repository>=<commit>"),
):
    """Say which repositories local CI answers for at their develop heads, and why not the rest.

    This is the registry's drift check: a step, action, expression or secret the registry does not
    account for leaves its job, and with it the repository, to GitHub.
    """
    from org.metadatacenter.local_ci.coverage import repository_coverage
    from org.metadatacenter.local_ci.registry import load_registry
    from org.metadatacenter.local_ci.run import parse_revisions, resolve_revision
    cedar_home = _cedar_home()
    try:
        registry = load_registry(cedar_home)
        revisions = parse_revisions(revision)
        names = list(repositories or registry.repositories())
        drift = False
        for name in names:
            commit = resolve_revision(cedar_home, name, revisions.get(name))
            coverage = repository_coverage(cedar_home, name, commit, registry)
            if not coverage.runs_ci and not coverage.problems:
                typer.echo(f"NO CI     {name} {commit[:8]}")
                continue
            legs = len(coverage.legs)
            if coverage.covered:
                typer.echo(f"COVERED   {name} {commit[:8]}  {legs} job leg(s)")
                continue
            listed = registry.entry(name) is not None
            declared = all(": not covered locally: " in reason for reason in coverage.reasons())
            drifted = listed and not declared
            drift = drift or drifted
            typer.echo(f"{'DRIFT' if drifted else 'GITHUB':9} {name} {commit[:8]}")
            for reason in coverage.reasons():
                typer.echo(f"          {reason}")
    except LocalCIError as error:
        typer.echo(f"Stopped: {error}")
        raise typer.Exit(1) from error
    raise typer.Exit(1 if drift else 0)


@app.command("status")
def status_command(
        repositories: list[str] = typer.Argument(None, help="Repositories; default every one the "
                                                          "registry names"),
):
    """Show local CI's answer for each repository's develop head."""
    from org.metadatacenter.local_ci.evidence import verdict
    from org.metadatacenter.local_ci.registry import load_registry
    from org.metadatacenter.local_ci.run import resolve_revision
    cedar_home = _cedar_home()
    try:
        registry = load_registry(cedar_home)
        for name in list(repositories or registry.repositories()):
            commit = resolve_revision(cedar_home, name, None)
            answer = verdict(cedar_home, name, commit, registry=registry)
            typer.echo(f"{answer.state.upper():6} {name} {commit[:8]}  {answer.detail}")
    except LocalCIError as error:
        typer.echo(f"Stopped: {error}")
        raise typer.Exit(1) from error

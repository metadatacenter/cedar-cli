import typer

from org.metadatacenter.util.CliResult import exit_on_failure
from org.metadatacenter.worker.RepoWorker import RepoWorker

app = typer.Typer(no_args_is_help=True)


@app.command("config", help="Show configured repos (in org/metadatacenter/config/ReposFactory.py)")
def repo_config():
    """Show the repositories selected by the effective CEDAR profile."""
    exit_on_failure(RepoWorker.repo_config())

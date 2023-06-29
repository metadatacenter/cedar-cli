import typer

from org.metadatacenter.worker.RepoWorker import RepoWorker

app = typer.Typer(no_args_is_help=True)


@app.command("config", help="Show configured repos (in org/metadatacenter/config/ReposFactory.py)")
def repo_config():
    RepoWorker.repo_config()

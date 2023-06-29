import typer

from org.metadatacenter.worker.EnvWorker import EnvWorker

app = typer.Typer(no_args_is_help=True)


@app.command("list", help="Lists all CEDAR environment variables")
def env_list():
    EnvWorker.list()


@app.command("core", help="Lists core CEDAR environment variables")
def core():
    EnvWorker.core()


@app.command("filter", help="Lists CEDAR environment variables that contain the passed filter term")
def filter(filter_term: str = typer.Argument('', help="Environment variable name to search for")):
    EnvWorker.filter(filter_term)

import typer

from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker
from org.metadatacenter.util.CliResult import exit_on_failure

app = typer.Typer(no_args_is_help=True)


@app.command("main")
def main():
    exit_on_failure(StopFrontendWorker.main())


@app.command("openview")
def openview():
    exit_on_failure(StopFrontendWorker.openview())


@app.command("monitoring")
def monitoring():
    exit_on_failure(StopFrontendWorker.monitoring())


@app.command("bridging")
def bridging():
    exit_on_failure(StopFrontendWorker.bridging())


@app.command("content")
def content():
    exit_on_failure(StopFrontendWorker.content())


@app.command("workspace")
def workspace():
    exit_on_failure(StopFrontendWorker.workspace())


@app.command("designer")
def designer():
    exit_on_failure(StopFrontendWorker.designer())


@app.command("split-frontends")
def split_frontends():
    exit_on_failure(StopFrontendWorker.split_frontends())


@app.command("all")
def frontend_all():
    exit_on_failure(StopFrontendWorker.all())

import typer

from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker
from org.metadatacenter.util.CliResult import exit_on_failure

app = typer.Typer(no_args_is_help=True)


@app.command("main")
def main():
    exit_on_failure(StartFrontendWorker.main())


@app.command("openview")
def openview():
    exit_on_failure(StartFrontendWorker.openview())


@app.command("monitoring")
def monitoring():
    exit_on_failure(StartFrontendWorker.monitoring())


@app.command("bridging")
def bridging():
    exit_on_failure(StartFrontendWorker.bridging())


@app.command("content")
def content():
    exit_on_failure(StartFrontendWorker.content())


@app.command("workspace")
def workspace():
    exit_on_failure(StartFrontendWorker.workspace())


@app.command("designer")
def designer():
    exit_on_failure(StartFrontendWorker.designer())


@app.command("split-frontends")
def split_frontends():
    exit_on_failure(StartFrontendWorker.split_frontends())


@app.command("all")
def frontend_all():
    exit_on_failure(StartFrontendWorker.all())

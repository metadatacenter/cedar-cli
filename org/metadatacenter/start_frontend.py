import typer

from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker

app = typer.Typer(no_args_is_help=True)


@app.command("main")
def main():
    StartFrontendWorker.main()


@app.command("openview")
def openview():
    StartFrontendWorker.openview()


@app.command("monitoring")
def monitoring():
    StartFrontendWorker.monitoring()


@app.command("bridging")
def bridging():
    StartFrontendWorker.bridging()


@app.command("content")
def content():
    StartFrontendWorker.content()


@app.command("workspace")
def workspace():
    StartFrontendWorker.workspace()


@app.command("designer")
def designer():
    StartFrontendWorker.designer()


@app.command("split-frontends")
def split_frontends():
    StartFrontendWorker.split_frontends()


@app.command("all")
def frontend_all():
    StartFrontendWorker.all()

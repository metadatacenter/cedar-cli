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


@app.command("artifacts")
def artifacts():
    StartFrontendWorker.artifacts()


@app.command("bridging")
def bridging():
    StartFrontendWorker.bridging()


@app.command("content")
def content():
    StartFrontendWorker.content()


@app.command("all")
def frontend_all():
    StartFrontendWorker.all()

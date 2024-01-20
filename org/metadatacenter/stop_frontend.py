import typer

from org.metadatacenter.worker.StopFrontendWorker import StopFrontendWorker

app = typer.Typer(no_args_is_help=True)


@app.command("main")
def main():
    StopFrontendWorker.main()


@app.command("openview")
def openview():
    StopFrontendWorker.openview()


@app.command("monitoring")
def monitoring():
    StopFrontendWorker.monitoring()


@app.command("artifacts")
def artifacts():
    StopFrontendWorker.artifacts()


@app.command("bridging")
def bridging():
    StopFrontendWorker.bridging()


@app.command("content")
def content():
    StopFrontendWorker.content()


@app.command("all")
def frontend_all():
    StopFrontendWorker.all()

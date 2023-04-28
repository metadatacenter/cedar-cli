import typer

from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker

app = typer.Typer(no_args_is_help=True)
start_worker = StartFrontendWorker()


@app.command("main")
def main():
    start_worker.main()


@app.command("openview")
def openview():
    start_worker.openview()


@app.command("monitoring")
def monitoring():
    start_worker.monitoring()


@app.command("artifacts")
def artifacts():
    start_worker.artifacts()


@app.command("all")
def all():
    start_worker.main()
    start_worker.openview()
    start_worker.monitoring()
    start_worker.artifacts()

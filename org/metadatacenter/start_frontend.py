import typer

from org.metadatacenter.worker.StartFrontendWorker import StartFrontendWorker

app = typer.Typer(no_args_is_help=True)
start_frontend_worker = StartFrontendWorker()


@app.command("main")
def main():
    start_frontend_worker.main()


@app.command("openview")
def openview():
    start_frontend_worker.openview()


@app.command("monitoring")
def monitoring():
    start_frontend_worker.monitoring()


@app.command("artifacts")
def artifacts():
    start_frontend_worker.artifacts()


@app.command("all")
def all():
    start_frontend_worker.main()
    start_frontend_worker.openview()
    start_frontend_worker.monitoring()
    start_frontend_worker.artifacts()

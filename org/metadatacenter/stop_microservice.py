import typer

from org.metadatacenter.worker.StopMicroserviceWorker import StopMicroserviceWorker

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def microservice_all():
    StopMicroserviceWorker.all()


@app.command("artifact")
def microservice_artifact():
    StopMicroserviceWorker.artifact()


@app.command("bridge")
def microservice_bridge():
    StopMicroserviceWorker.bridge()


@app.command("group")
def microservice_group():
    StopMicroserviceWorker.group()


@app.command("impex")
def microservice_impex():
    StopMicroserviceWorker.impex()


@app.command("messaging")
def microservice_messaging():
    StopMicroserviceWorker.messaging()


@app.command("monitor")
def microservice_monitor():
    StopMicroserviceWorker.monitor()


@app.command("open")
def microservice_open():
    StopMicroserviceWorker.open()


@app.command("repo")
def microservice_repo():
    StopMicroserviceWorker.repo()


@app.command("resource")
def microservice_resource():
    StopMicroserviceWorker.resource()


@app.command("schema")
def microservice_schema():
    StopMicroserviceWorker.schema()


@app.command("submission")
def microservice_submission():
    StopMicroserviceWorker.submission()


@app.command("terminology")
def microservice_terminology():
    StopMicroserviceWorker.terminology()


@app.command("user")
def microservice_user():
    StopMicroserviceWorker.user()


@app.command("valuerecommender")
def microservice_valuerecommender():
    StopMicroserviceWorker.valuerecommender()


@app.command("worker")
def microservice_worker():
    StopMicroserviceWorker.worker()

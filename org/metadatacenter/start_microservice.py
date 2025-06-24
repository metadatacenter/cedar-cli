import typer

from org.metadatacenter.worker.StartMicroserviceWorker import StartMicroserviceWorker

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def microservice_all():
    StartMicroserviceWorker.all()


@app.command("artifact")
def microservice_artifact():
    StartMicroserviceWorker.artifact()


@app.command("bridge")
def microservice_bridge():
    StartMicroserviceWorker.bridge()


@app.command("group")
def microservice_group():
    StartMicroserviceWorker.group()


@app.command("impex")
def microservice_impex():
    StartMicroserviceWorker.impex()


@app.command("messaging")
def microservice_messaging():
    StartMicroserviceWorker.messaging()


@app.command("monitor")
def microservice_monitor():
    StartMicroserviceWorker.monitor()


@app.command("open")
def microservice_open():
    StartMicroserviceWorker.open()


@app.command("repo")
def microservice_repo():
    StartMicroserviceWorker.repo()


@app.command("resource")
def microservice_resource():
    StartMicroserviceWorker.resource()


@app.command("schema")
def microservice_schema():
    StartMicroserviceWorker.schema()


@app.command("submission")
def microservice_submission():
    StartMicroserviceWorker.submission()


@app.command("terminology")
def microservice_terminology():
    StartMicroserviceWorker.terminology()


@app.command("user")
def microservice_user():
    StartMicroserviceWorker.user()


@app.command("valuerecommender")
def microservice_valuerecommender():
    StartMicroserviceWorker.valuerecommender()


@app.command("worker")
def microservice_worker():
    StartMicroserviceWorker.worker()

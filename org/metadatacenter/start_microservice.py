import typer

from org.metadatacenter.worker.StartMicroserviceWorker import StartMicroserviceWorker
from org.metadatacenter.util.CliResult import exit_on_failure

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def microservice_all():
    exit_on_failure(StartMicroserviceWorker.all())


@app.command("artifact")
def microservice_artifact():
    exit_on_failure(StartMicroserviceWorker.artifact())


@app.command("bridge")
def microservice_bridge():
    exit_on_failure(StartMicroserviceWorker.bridge())


@app.command("group")
def microservice_group():
    exit_on_failure(StartMicroserviceWorker.group())


@app.command("impex")
def microservice_impex():
    exit_on_failure(StartMicroserviceWorker.impex())


@app.command("messaging")
def microservice_messaging():
    exit_on_failure(StartMicroserviceWorker.messaging())


@app.command("monitor")
def microservice_monitor():
    exit_on_failure(StartMicroserviceWorker.monitor())


@app.command("open")
def microservice_open():
    exit_on_failure(StartMicroserviceWorker.open())


@app.command("repo")
def microservice_repo():
    exit_on_failure(StartMicroserviceWorker.repo())


@app.command("resource")
def microservice_resource():
    exit_on_failure(StartMicroserviceWorker.resource())


@app.command("schema")
def microservice_schema():
    exit_on_failure(StartMicroserviceWorker.schema())


@app.command("submission")
def microservice_submission():
    exit_on_failure(StartMicroserviceWorker.submission())


@app.command("terminology")
def microservice_terminology():
    exit_on_failure(StartMicroserviceWorker.terminology())


@app.command("user")
def microservice_user():
    exit_on_failure(StartMicroserviceWorker.user())


@app.command("valuerecommender")
def microservice_valuerecommender():
    exit_on_failure(StartMicroserviceWorker.valuerecommender())


@app.command("worker")
def microservice_worker():
    exit_on_failure(StartMicroserviceWorker.worker())

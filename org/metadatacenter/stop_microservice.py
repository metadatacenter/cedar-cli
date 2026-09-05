import typer

from org.metadatacenter.worker.StopMicroserviceWorker import StopMicroserviceWorker
from org.metadatacenter.util.CliResult import exit_on_failure

app = typer.Typer(no_args_is_help=True)


@app.command("all")
def microservice_all():
    exit_on_failure(StopMicroserviceWorker.all())


@app.command("artifact")
def microservice_artifact():
    exit_on_failure(StopMicroserviceWorker.artifact())


@app.command("bridge")
def microservice_bridge():
    exit_on_failure(StopMicroserviceWorker.bridge())


@app.command("group")
def microservice_group():
    exit_on_failure(StopMicroserviceWorker.group())


@app.command("impex")
def microservice_impex():
    exit_on_failure(StopMicroserviceWorker.impex())


@app.command("messaging")
def microservice_messaging():
    exit_on_failure(StopMicroserviceWorker.messaging())


@app.command("monitor")
def microservice_monitor():
    exit_on_failure(StopMicroserviceWorker.monitor())


@app.command("openview")
def microservice_openview():
    exit_on_failure(StopMicroserviceWorker.openview())


# The command was once named for its function rather than for the service it stops, which left it
# the one name in this group that does not match the service. Keep the old spelling working, and
# out of the help, for anyone whose fingers or scripts still reach for it.
@app.command("open", hidden=True)
def microservice_open():
    exit_on_failure(StopMicroserviceWorker.openview())


@app.command("repo")
def microservice_repo():
    exit_on_failure(StopMicroserviceWorker.repo())


@app.command("resource")
def microservice_resource():
    exit_on_failure(StopMicroserviceWorker.resource())


@app.command("schema")
def microservice_schema():
    exit_on_failure(StopMicroserviceWorker.schema())


@app.command("submission")
def microservice_submission():
    exit_on_failure(StopMicroserviceWorker.submission())


@app.command("terminology")
def microservice_terminology():
    exit_on_failure(StopMicroserviceWorker.terminology())


@app.command("user")
def microservice_user():
    exit_on_failure(StopMicroserviceWorker.user())


@app.command("valuerecommender")
def microservice_valuerecommender():
    exit_on_failure(StopMicroserviceWorker.valuerecommender())


@app.command("worker")
def microservice_worker():
    exit_on_failure(StopMicroserviceWorker.worker())

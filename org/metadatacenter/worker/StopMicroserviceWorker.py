from rich.console import Console

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StopMicroserviceWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def all():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('stop-microservices.scpt')],
                title="Stopping Microservices",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('stop-microservices.sh')],
                title="Stopping Microservices",
            )

    @staticmethod
    def artifact():
        StopMicroserviceWorker._stop("artifact")

    @staticmethod
    def bridge():
        StopMicroserviceWorker._stop("bridge")

    @staticmethod
    def group():
        StopMicroserviceWorker._stop("group")

    @staticmethod
    def impex():
        StopMicroserviceWorker._stop("impex")

    @staticmethod
    def messaging():
        StopMicroserviceWorker._stop("messaging")

    @staticmethod
    def monitor():
        StopMicroserviceWorker._stop("monitor")

    @staticmethod
    def open():
        StopMicroserviceWorker._stop("open")

    @staticmethod
    def repo():
        StopMicroserviceWorker._stop("repo")

    @staticmethod
    def resource():
        StopMicroserviceWorker._stop("resource")

    @staticmethod
    def schema():
        StopMicroserviceWorker._stop("schema")

    @staticmethod
    def submission():
        StopMicroserviceWorker._stop("submission")

    @staticmethod
    def terminology():
        StopMicroserviceWorker._stop("terminology")

    @staticmethod
    def user():
        StopMicroserviceWorker._stop("user")

    @staticmethod
    def valuerecommender():
        StopMicroserviceWorker._stop("valuerecommender")

    @staticmethod
    def worker():
        StopMicroserviceWorker._stop("worker")

    @staticmethod
    def _stop(service_name: str):
        title = f"Stopping {service_name.capitalize()} Microservice"
        if GlobalContext.get_use_osa():
            script = f"stop-microservice-{service_name}.scpt"
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path(script)],
                title=title,
            )
        else:
            script = f"stop-microservice-{service_name}.sh"
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path(script)],
                title=title,
            )

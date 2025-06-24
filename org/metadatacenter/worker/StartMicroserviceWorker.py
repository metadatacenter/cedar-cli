from rich.console import Console

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StartMicroserviceWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def all():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-microservices-new-tab.scpt')],
                title="Launching Microservices in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-microservices.sh')],
                title="Launching Microservices",
            )

    @staticmethod
    def artifact():
        StartMicroserviceWorker._start("artifact")

    @staticmethod
    def bridge():
        StartMicroserviceWorker._start("bridge")

    @staticmethod
    def group():
        StartMicroserviceWorker._start("group")

    @staticmethod
    def impex():
        StartMicroserviceWorker._start("impex")

    @staticmethod
    def messaging():
        StartMicroserviceWorker._start("messaging")

    @staticmethod
    def monitor():
        StartMicroserviceWorker._start("monitor")

    @staticmethod
    def open():
        StartMicroserviceWorker._start("open")

    @staticmethod
    def repo():
        StartMicroserviceWorker._start("repo")

    @staticmethod
    def resource():
        StartMicroserviceWorker._start("resource")

    @staticmethod
    def schema():
        StartMicroserviceWorker._start("schema")

    @staticmethod
    def submission():
        StartMicroserviceWorker._start("submission")

    @staticmethod
    def terminology():
        StartMicroserviceWorker._start("terminology")

    @staticmethod
    def user():
        StartMicroserviceWorker._start("user")

    @staticmethod
    def valuerecommender():
        StartMicroserviceWorker._start("valuerecommender")

    @staticmethod
    def worker():
        StartMicroserviceWorker._start("worker")

    @staticmethod
    def _start(service_name: str):
        title = f"Launching {service_name.capitalize()} Microservice"
        if GlobalContext.get_use_osa():
            script = f"start-microservice-{service_name}-new-tab.scpt"
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path(script)],
                title=title + " in new tab",
            )
        else:
            script = f"start-microservice-{service_name}.sh"
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path(script)],
                title=title,
            )

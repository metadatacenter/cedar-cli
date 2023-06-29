from rich.console import Console

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StartMicroserviceWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def all():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-microservices-new-tab.scpt')],
            title="Launching Microservices in new tab",
        )

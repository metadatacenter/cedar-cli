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
    def bridge():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-microservice-bridge-new-tab.scpt')],
                title="Launching Bridge Server in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-microservice-bridge.sh')],
                title="Launching Bridge Server",
            )

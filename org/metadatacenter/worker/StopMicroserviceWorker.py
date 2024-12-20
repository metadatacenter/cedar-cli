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
    def bridge():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('stop-microservice-bridge.scpt')],
                title="Stopping Bridge Server",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('stop-microservice-bridge.sh')],
                title="Stopping Bridge Server",
            )

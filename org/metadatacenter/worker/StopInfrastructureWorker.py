from rich.console import Console

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StopInfrastructureWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def all():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('stop-infrastructure.scpt')],
            title="Stopping Infrastructure services",
        )

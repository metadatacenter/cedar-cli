from rich.console import Console

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StartInfrastructureWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def all():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-infrastructure-all-new-tab.scpt')],
                title="Launching Infrastructure services in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-infrastructure-all.sh')],
                title="Launching Infrastructure services",
            )


    @staticmethod
    def keycloak():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-infrastructure-keycloak-new-tab.scpt')],
                title="Launching Keycloak services in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-infrastructure-keycloak.sh')],
                title="Launching Keycloak services",
            )

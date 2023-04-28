from rich.console import Console

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StartFrontendWorker(Worker):

    def __init__(self):
        super().__init__()

    def openview(self):
        self.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-openview-new-tab.scpt')],
            title="Launching OpenView Frontend in new tab",
        )

    def monitoring(self):
        self.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-monitoring-new-tab.scpt')],
            title="Launching Monitoring Frontend in new tab",
        )

    def artifacts(self):
        self.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-artifacts-new-tab.scpt')],
            title="Launching Artifacts Frontend in new tab",
        )

    def main(self):
        self.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-main-new-tab.scpt')],
            title="Launching Main Frontend in new tab",
        )

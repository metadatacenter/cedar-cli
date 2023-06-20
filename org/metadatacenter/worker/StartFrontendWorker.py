from rich.console import Console

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StartFrontendWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def openview():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-openview-new-tab.scpt')],
            title="Launching OpenView Frontend in new tab",
        )

    @staticmethod
    def monitoring():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-monitoring-new-tab.scpt')],
            title="Launching Monitoring Frontend in new tab",
        )

    @staticmethod
    def bridging():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-bridging-new-tab.scpt')],
            title="Launching Bridging Frontend in new tab",
        )

    @staticmethod
    def artifacts():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-artifacts-new-tab.scpt')],
            title="Launching Artifacts Frontend in new tab",
        )

    @staticmethod
    def component():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-component-new-tab.scpt')],
            title="Launching Component Frontend in new tab",
        )

    @staticmethod
    def main():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('start-frontend-main-new-tab.scpt')],
            title="Launching Main Frontend in new tab",
        )

    @staticmethod
    def all():
        StartFrontendWorker.main()
        StartFrontendWorker.openview()
        StartFrontendWorker.monitoring()
        StartFrontendWorker.artifacts()
        StartFrontendWorker.bridging()
        StartFrontendWorker.component()

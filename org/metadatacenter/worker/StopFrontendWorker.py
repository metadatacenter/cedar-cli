from rich.console import Console

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StopFrontendWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def openview():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('stop-frontend-openview.scpt')],
            title="Stopping OpenView Frontend",
        )

    @staticmethod
    def monitoring():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('stop-frontend-monitoring.scpt')],
            title="Stopping Monitoring Frontend",
        )

    @staticmethod
    def bridging():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('stop-frontend-bridging.scpt')],
            title="Stopping Bridging Frontend",
        )

    @staticmethod
    def artifacts():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('stop-frontend-artifacts.scpt')],
            title="Stopping Artifacts Frontend",
        )

    @staticmethod
    def content():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('stop-frontend-content.scpt')],
            title="Stopping Content Frontend",
        )

    @staticmethod
    def main():
        Worker.execute_generic_shell_commands(
            ["osascript " + Util.get_osa_script_path('stop-frontend-main.scpt')],
            title="Stopping Main Frontend",
        )

    @staticmethod
    def all():
        StopFrontendWorker.main()
        StopFrontendWorker.openview()
        StopFrontendWorker.monitoring()
        StopFrontendWorker.artifacts()
        StopFrontendWorker.bridging()
        StopFrontendWorker.content()

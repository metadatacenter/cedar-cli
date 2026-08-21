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
    def workspace():
        Worker.execute_generic_shell_commands(
            ["source " + Util.get_bash_script_path('stop-frontend-workspace.sh')],
            title="Stopping Workspace Preview",
        )

    @staticmethod
    def designer():
        Worker.execute_generic_shell_commands(
            ["source " + Util.get_bash_script_path('stop-frontend-designer.sh')],
            title="Stopping Template Designer Preview",
        )

    @staticmethod
    def all():
        # Preview processes are deliberately not part of production-era bulk stop operations.
        StopFrontendWorker.main()
        StopFrontendWorker.openview()
        StopFrontendWorker.monitoring()
        StopFrontendWorker.bridging()
        StopFrontendWorker.content()

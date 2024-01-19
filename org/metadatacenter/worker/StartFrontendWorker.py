from rich.console import Console

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class StartFrontendWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def openview():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-frontend-openview-new-tab.scpt')],
                title="Launching OpenView Frontend in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-frontend-openview.sh')],
                title="Launching OpenView Frontend",
            )

    @staticmethod
    def monitoring():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-frontend-monitoring-new-tab.scpt')],
                title="Launching Monitoring Frontend in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-frontend-monitoring.sh')],
                title="Launching Monitoring Frontend",
            )

    @staticmethod
    def bridging():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-frontend-bridging-new-tab.scpt')],
                title="Launching Bridging Frontend in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-frontend-bridging.sh')],
                title="Launching Bridging Frontend",
            )

    @staticmethod
    def artifacts():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-frontend-artifacts-new-tab.scpt')],
                title="Launching Artifacts Frontend in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-frontend-artifacts.sh')],
                title="Launching Artifacts Frontend",
            )

    @staticmethod
    def content():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-frontend-content-new-tab.scpt')],
                title="Launching Content Frontend in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-frontend-content.sh')],
                title="Launching Content Frontend",
            )

    @staticmethod
    def main():
        if GlobalContext.get_use_osa():
            Worker.execute_generic_shell_commands(
                ["osascript " + Util.get_osa_script_path('start-frontend-main-new-tab.scpt')],
                title="Launching Main Frontend in new tab",
            )
        else:
            Worker.execute_generic_shell_commands(
                ["source " + Util.get_bash_script_path('start-frontend-main.sh')],
                title="Launching Main Frontend",
            )

    @staticmethod
    def all():
        StartFrontendWorker.main()
        StartFrontendWorker.openview()
        StartFrontendWorker.monitoring()
        StartFrontendWorker.artifacts()
        StartFrontendWorker.bridging()
        StartFrontendWorker.content()

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker


class StartInfrastructureWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def all():
        return Worker.execute_generic_shell_commands(
            ["source " + Util.get_bash_script_path('start-infrastructure-all.sh')],
            title="Starting native infrastructure services",
        )


    @staticmethod
    def keycloak():
        return Worker.execute_generic_shell_commands(
            ["source " + Util.get_bash_script_path('start-infrastructure-keycloak.sh')],
            title="Starting native Keycloak service",
        )

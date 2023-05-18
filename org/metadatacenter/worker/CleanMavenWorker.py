from rich.console import Console

from org.metadatacenter.worker.Worker import Worker

console = Console()


class CleanMavenWorker(Worker):

    def __init__(self):
        super().__init__()

    def all(self):
        self.execute_generic_shell_commands(
            ["rm -rf ~/.m2/repository/"],
            title="Removing all local maven repository content",
        )

    def cedar(self):
        self.execute_generic_shell_commands(
            ["rm -rf ~/.m2/repository/org/metadatacenter/"],
            title="Removing org.metadatacenter local maven repository content",
        )

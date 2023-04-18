from rich.console import Console

from org.metadatacenter.Repos import Repos
from org.metadatacenter.Worker import Worker

console = Console()


class BuildWorker(Worker):
    def __init__(self, repos: Repos):
        super().__init__(repos)

    def parent(self):
        console.print("Build parent")
        self.execute_shell(self.repos.get_parent(), ["mvn clean install -DskipTests"])

    def libraries(self):
        console.print("Build libraries")
        self.execute_shell(self.repos.get_libraries(), ["mvn clean install -DskipTests"])

    def project(self):
        console.print("Build project")
        self.execute_shell(self.repos.get_project(), ["mvn clean install -DskipTests"])

    def clients(self):
        console.print("Build clients")
        self.execute_shell(self.repos.get_clients(), ["mvn clean install -DskipTests"])

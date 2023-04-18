from rich.console import Console
from rich.panel import Panel

from org.metadatacenter.Repos import Repos
from org.metadatacenter.Worker import Worker

console = Console()


class BuildWorker(Worker):
    def __init__(self, repos: Repos):
        super().__init__(repos)

    def parent(self):
        console.print(Panel("Build parent"))
        self.execute_shell(self.repos.get_parent(), ["mvn clean install -DskipTests"], "Building parent")

    def libraries(self):
        console.print(Panel("Build libraries"))
        self.execute_shell(self.repos.get_libraries(), ["mvn clean install -DskipTests"], "Building libraries")

    def project(self):
        console.print(Panel("Build project"))
        self.execute_shell(self.repos.get_project(), ["mvn clean install -DskipTests"], "Building project")

    def clients(self):
        console.print(Panel("Build clients"))
        self.execute_shell(self.repos.get_clients(), ["mvn clean install -DskipTests"], "Building clients")

    def frontends(self):
        console.print(Panel("Build frontends"))
        frontend_list = self.repos.get_angular_frontends()
        for repo in frontend_list:
            self.execute_shell(repo, ["pwd; npm install --legacy-peer-deps; ng build --configuration=production"], "Building frontends")

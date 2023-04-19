from rich.console import Console
from rich.panel import Panel
from rich.style import Style

from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.Repos import Repos
from org.metadatacenter.worker.Worker import Worker

console = Console()


class BuildWorker(Worker):
    def __init__(self, repos: Repos):
        super().__init__(repos)

    def parent(self):
        self.build_repos([self.repos.get_parent()], "Build parent", "Building parent")

    def libraries(self):
        self.build_repos([self.repos.get_libraries()], "Build libraries", "Building libraries")

    def project(self):
        self.build_repos([self.repos.get_project()], "Build project", "Building project")

    def clients(self):
        self.build_repos([self.repos.get_clients()], "Build clients", "Building clients")

    def frontends(self):
        self.build_repos(self.repos.get_frontends(), "Build frontends", "Building frontends")

    def build_repos(self, repo_list, title, progress_text):
        msg = "[cyan]" + title
        repo_list_flat = self.get_flat_repo_list(repo_list)
        for repo in repo_list_flat:
            msg += "\n  " + "️ ➡️  " + repo.get_wd()
        console.print(Panel(msg, style=Style(color="cyan")))
        for repo in repo_list_flat:
            # console.print("Build: " + (repo.parent_repo.name + " " if repo.is_sub_repo else "") + repo.name + " T:" + repo.repo_type)
            if repo.repo_type == RepoType.JAVA_WRAPPER:
                self.execute_shell(repo, ["mvn clean install -DskipTests"], progress_text)
            elif repo.repo_type == RepoType.ANGULAR:
                self.execute_shell(repo, ["pwd; npm install --legacy-peer-deps; ng build --configuration=production"], progress_text)
            elif repo.repo_type == RepoType.ANGULAR_JS:
                self.execute_shell(repo, ["pwd; npm install"], progress_text)

    def this(self, wd: str):
        repos = []
        for repo in self.repos.get_list_all():
            if self.get_wd(repo) == wd:
                repos.append(repo)
        self.build_repos(repos, "Build this", "Building this")

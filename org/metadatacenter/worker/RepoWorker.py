from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.Worker import Worker

console = Console()


class RepoWorker(Worker):
    def __init__(self):
        super().__init__()

    def list_repos(self):
        table = Table("Repo",
                      Column(header="Type", justify="center"),
                      Column(header="Library", justify="center"),
                      Column(header="Client", justify="center"),
                      Column(header="Microservice", justify="center"),
                      Column(header="Frontend", justify="center"),
                      Column(header="Private", justify="center"),
                      Column(header="Docker", justify="center"))
        for repo in GlobalContext.repos.get_list_all():
            self.add_table_row(repo, table)
        console.print(table)

    def add_table_row(self, repo, table):
        is_library = "✅" if repo.is_library else ""
        is_client = "✅" if repo.is_client else ""
        is_microservice = "✅" if repo.is_microservice else ""
        is_private = "✅" if repo.is_private else ""
        for_docker = "✅" if repo.for_docker else ""
        is_frontend = "✅" if repo.is_frontend else ""
        name = repo.parent_repo.name + "️ ➡️  " + repo.name if repo.is_sub_repo else repo.name
        table.add_row(name, repo.repo_type, is_library, is_client, is_microservice, is_frontend, is_private, for_docker)

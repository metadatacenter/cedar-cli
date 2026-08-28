import os
from pathlib import Path

from rich.console import Console
from rich.table import Table, Column

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()

STATUS_ICON_OK = '✅'
STATUS_ICON_UNKNOWN = '❓'
STATUS_ICON_MISSING = '❌'

class RepoWorker(Worker):
    def __init__(self):
        super().__init__()

    @staticmethod
    def repo_config():
        table = Table("Repo",
                      Column(header="Type", justify="center"),
                      Column(header="Library", justify="center"),
                      Column(header="Microservice", justify="center"),
                      Column(header="Frontend", justify="center"),
                      Column(header="Private", justify="center"),
                      Column(header="Docker", justify="center"))
        for repo in sorted(GlobalContext.repos.get_list_all(), key=lambda item: item.get_fqn()):
            RepoWorker.add_repo_list_row(repo, table)
        console.print(table)
        return 0

    @staticmethod
    def add_repo_list_row(repo, table):
        is_library = STATUS_ICON_OK if repo.is_library else ""
        is_microservice = STATUS_ICON_OK if repo.is_microservice else ""
        is_private = STATUS_ICON_OK if repo.is_private else ""
        for_docker = STATUS_ICON_OK if repo.for_docker else ""
        is_frontend = STATUS_ICON_OK if repo.is_frontend else ""
        name = repo.parent_repo.name + ' ⮕ ' + repo.name if repo.is_sub_repo else repo.name
        table.add_row(name, repo.repo_type, is_library, is_microservice, is_frontend, is_private, for_docker)

    @staticmethod
    def check_repos():
        """Check the configured repository inventory and report unmanaged Git clones."""
        table = Table(
            "Repository",
            Column(header="Type", justify="center"),
            Column(header="Registration"),
            Column(header="Status", justify="center"),
        )

        repos = sorted(GlobalContext.repos.get_list_all(), key=lambda item: item.get_fqn())
        configured_top_level = {repo.name for repo in repos if not repo.is_sub_repo}
        present_count = 0
        missing = []

        for repo in repos:
            present = RepoWorker.get_repo_dir_status(repo)
            if present:
                present_count += 1
            else:
                missing.append(repo.get_fqn())
            table.add_row(
                repo.get_fqn(),
                str(repo.repo_type),
                "configured",
                STATUS_ICON_OK if present else STATUS_ICON_MISSING,
            )

        unmanaged = []
        cedar_home = Path(Util.cedar_home)
        if cedar_home.is_dir():
            for path in sorted(cedar_home.iterdir(), key=lambda item: item.name):
                if path.is_dir() and (path / ".git").exists() and path.name not in configured_top_level:
                    unmanaged.append(path.name)
                    table.add_row(path.name, "git", "unmanaged clone", STATUS_ICON_UNKNOWN)

        caption = f"{present_count}/{len(repos)} configured repositories present"
        if missing:
            caption += f"\n[red]{len(missing)} missing: {', '.join(missing)}[/red]"
        if unmanaged:
            caption += f"\n[yellow]{len(unmanaged)} unmanaged Git clone(s): {', '.join(unmanaged)}[/yellow]"
        table.caption = caption

        console.print(table)
        return 1 if missing else 0

    @staticmethod
    def get_repo_dir_status(repo):
        if os.path.isdir(Util.get_wd(repo)):
            return True
        else:
            return False

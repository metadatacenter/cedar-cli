from rich.console import Console

from org.metadatacenter.assembler.Assembler import Assembler
from org.metadatacenter.model.Task import Task
from org.metadatacenter.model.WorkerType import WorkerType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

console = Console()


class DeployAssembler(Assembler):
    def __init__(self):
        super().__init__()

    def parent(self):
        self.add_task(Task(WorkerType.DEPLOY, [GlobalContext.repos.get_parent()], "Deploy parent", "Deploying parent"))

    def libraries(self):
        self.add_task(Task(WorkerType.DEPLOY, [GlobalContext.repos.get_libraries()], "Deploy libraries", "Deploying libraries"))

    def project(self):
        self.add_task(Task(WorkerType.DEPLOY, [GlobalContext.repos.get_project()], "Deploy project", "Deploying project"))

    def clients(self):
        self.add_task(Task(WorkerType.DEPLOY, [GlobalContext.repos.get_clients()], "Deploy clients", "Deploying clients"))

    def frontends(self):
        self.add_task(Task(WorkerType.DEPLOY, GlobalContext.repos.get_frontends(), "Deploy frontends", "Deploying frontends"))

    def this(self, wd: str):
        for repo in GlobalContext.repos.get_list_all():
            if Util.get_wd(repo) == wd:
                self.add_task(Task(WorkerType.DEPLOY, [repo], "Deploy this", "Deploying this"))

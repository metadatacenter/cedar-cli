from rich.console import Console

from org.metadatacenter.assembler.Assembler import Assembler
from org.metadatacenter.model.Task import Task
from org.metadatacenter.model.WorkerType import WorkerType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util

console = Console()


class BuildAssembler(Assembler):
    def __init__(self):
        super().__init__()

    def parent(self):
        self.add_task(Task(WorkerType.BUILD, [GlobalContext.repos.get_parent()], "Build parent", "Building parent"))

    def libraries(self):
        self.add_task(Task(WorkerType.BUILD, [GlobalContext.repos.get_libraries()], "Build libraries", "Building libraries"))

    def project(self):
        self.add_task(Task(WorkerType.BUILD, [GlobalContext.repos.get_project()], "Build project", "Building project"))

    def clients(self):
        self.add_task(Task(WorkerType.BUILD, [GlobalContext.repos.get_clients()], "Build clients", "Building clients"))

    def frontends(self):
        self.add_task(Task(WorkerType.BUILD, GlobalContext.repos.get_frontends(), "Build frontends", "Building frontends"))

    def this(self, wd: str):
        for repo in GlobalContext.repos.get_list_all():
            if Util.get_wd(repo) == wd:
                self.add_task(Task(WorkerType.BUILD, [repo], "Build this", "Building this"))

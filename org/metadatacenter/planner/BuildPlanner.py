from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util


class BuildPlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def parent(plan: Plan):
        plan.add_task(
            "Build parent",
            TaskType.BUILD,
            GlobalContext.repos.get_parent()
        )

    @staticmethod
    def libraries(plan: Plan):
        plan.add_task(
            "Build libraries",
            TaskType.BUILD,
            GlobalContext.repos.get_libraries()
        )

    @staticmethod
    def project(plan: Plan):
        plan.add_task(
            "Build project",
            TaskType.BUILD,
            GlobalContext.repos.get_project()
        )

    @staticmethod
    def clients(plan: Plan):
        plan.add_task(
            "Build clients",
            TaskType.BUILD,
            GlobalContext.repos.get_clients()
        )

    @staticmethod
    def frontends(plan: Plan):
        plan.add_task(
            "Build frontends",
            TaskType.BUILD,
            GlobalContext.repos.get_frontends(),
            parameters={"force_frontend_build": True},
        )
        # Full reactor completion includes the owning packages' verification gates.
        from org.metadatacenter.frontend_inventory import reactor_checks
        checks = reactor_checks()
        def verify(task):
            if getattr(task, 'parameters', {}).get('isolated_frontend_build'):
                if task.repo.name not in checks:
                    raise ValueError(f"Frontend {task.repo.name} has no verification inventory entry")
                task.command_list = checks[task.repo.name]
            for child in task.tasks:
                verify(child)
        verify(plan)

    @staticmethod
    def split_frontends(plan: Plan, server_payload: bool = False):
        plan.add_task(
            "Build split frontends",
            TaskType.BUILD,
            GlobalContext.repos.get_split_frontends(),
            parameters={"force_frontend_build": True, "server_frontend_payload": server_payload}
        )

    @staticmethod
    def server_frontends(plan: Plan, server_payload: bool = False):
        plan.add_task(
            "Build server frontends",
            TaskType.BUILD,
            GlobalContext.repos.get_server_frontends(),
            parameters={"force_frontend_build": True, "server_frontend_payload": server_payload}
        )

    @staticmethod
    def this(plan: Plan, wd: str):
        for repo in GlobalContext.repos.get_list_all():
            if Util.get_wd(repo).lower() == wd.lower():
                plan.add_task(
                    "Build current repo",
                    TaskType.BUILD,
                    [repo]
                )

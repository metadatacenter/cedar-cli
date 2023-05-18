from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util


class BuildPlanner(Planner):

    def __init__(self):
        super().__init__()

    def parent(self, plan: Plan):
        plan.add_task(
            "Build parent",
            TaskType.BUILD,
            GlobalContext.repos.get_parent()
        )

    def libraries(self, plan: Plan):
        plan.add_task(
            "Build libraries",
            TaskType.BUILD,
            GlobalContext.repos.get_libraries()
        )

    def project(self, plan: Plan):
        plan.add_task(
            "Build project",
            TaskType.BUILD,
            GlobalContext.repos.get_project()
        )

    def clients(self, plan: Plan):
        plan.add_task(
            "Build clients",
            TaskType.BUILD,
            GlobalContext.repos.get_clients()
        )

    def frontends(self, plan: Plan):
        plan.add_task(
            "Build frontends",
            TaskType.BUILD,
            GlobalContext.repos.get_frontends()
        )

    def this(self, plan: Plan, wd: str):
        for repo in GlobalContext.repos.get_list_all():
            if Util.get_wd(repo) == wd:
                plan.add_task(
                    "Build current repo",
                    TaskType.BUILD,
                    [repo]
                )

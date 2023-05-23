from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util


class DeployPlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def parent(plan: Plan):
        plan.add_task(
            "Deploy parent",
            TaskType.DEPLOY,
            GlobalContext.repos.get_parent()
        )

    @staticmethod
    def libraries(plan: Plan):
        plan.add_task(
            "Deploy libraries",
            TaskType.DEPLOY,
            GlobalContext.repos.get_libraries()
        )

    @staticmethod
    def project(plan: Plan):
        plan.add_task(
            "Deploy project",
            TaskType.DEPLOY,
            GlobalContext.repos.get_project()
        )

    @staticmethod
    def clients(plan: Plan):
        plan.add_task(
            "Deploy clients",
            TaskType.DEPLOY,
            GlobalContext.repos.get_clients()
        )

    @staticmethod
    def frontends(plan: Plan):
        plan.add_task(
            "Deploy frontends",
            TaskType.DEPLOY,
            GlobalContext.repos.get_frontends()
        )

    @staticmethod
    def this(plan: Plan, wd: str):
        for repo in GlobalContext.repos.get_list_all():
            if Util.get_wd(repo) == wd:
                plan.add_task(
                    "Deploy current repo",
                    TaskType.DEPLOY,
                    [repo]
                )

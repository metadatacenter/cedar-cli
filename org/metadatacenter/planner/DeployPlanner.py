from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util


class DeployPlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def parent(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Deploy parent",
            TaskType.DEPLOY,
            GlobalContext.repos.get_parent(),
            parameters
        )

    @staticmethod
    def libraries(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Deploy libraries",
            TaskType.DEPLOY,
            GlobalContext.repos.get_libraries(),
            parameters
        )

    @staticmethod
    def project(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Deploy project",
            TaskType.DEPLOY,
            GlobalContext.repos.get_project(),
            parameters
        )

    @staticmethod
    def clients(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Deploy clients",
            TaskType.DEPLOY,
            GlobalContext.repos.get_clients(),
            parameters
        )

    @staticmethod
    def frontends(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Deploy frontends",
            TaskType.DEPLOY,
            GlobalContext.repos.get_frontends(),
            parameters
        )

    @staticmethod
    def this(plan: Plan, wd: str):
        for repo in GlobalContext.repos.get_list_all():
            if Util.get_wd(repo).lower() == wd.lower():
                plan.add_task(
                    "Deploy current repo",
                    TaskType.DEPLOY,
                    [repo]
                )

    @staticmethod
    def all(plan: Plan):
        DeployPlanner.parent(plan)
        DeployPlanner.libraries(plan)
        DeployPlanner.project(plan)
        DeployPlanner.clients(plan)
        DeployPlanner.frontends(plan)

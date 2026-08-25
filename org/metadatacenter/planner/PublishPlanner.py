from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.Util import Util


class PublishPlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def parent(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Publish parent",
            TaskType.PUBLISH,
            GlobalContext.repos.get_parent(),
            parameters
        )

    @staticmethod
    def libraries(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Publish libraries",
            TaskType.PUBLISH,
            GlobalContext.repos.get_libraries(),
            parameters
        )

    @staticmethod
    def project(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Publish project",
            TaskType.PUBLISH,
            GlobalContext.repos.get_project(),
            parameters
        )

    @staticmethod
    def clients(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Publish clients",
            TaskType.PUBLISH,
            GlobalContext.repos.get_clients(),
            parameters
        )

    @staticmethod
    def frontends(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Publish frontends",
            TaskType.PUBLISH,
            GlobalContext.repos.get_frontends_for_default_publish(),
            parameters
        )

    @staticmethod
    def split_frontends(plan: Plan, parameters: dict = None):
        plan.add_task(
            "Publish split frontends",
            TaskType.PUBLISH,
            GlobalContext.repos.get_split_frontends(),
            parameters
        )

    @staticmethod
    def this(plan: Plan, wd: str):
        for repo in GlobalContext.repos.get_list_all():
            if Util.get_wd(repo).lower() == wd.lower():
                plan.add_task(
                    "Publish current repo",
                    TaskType.PUBLISH,
                    [repo]
                )

    @staticmethod
    def all(plan: Plan):
        PublishPlanner.parent(plan)
        PublishPlanner.libraries(plan)
        PublishPlanner.project(plan)
        PublishPlanner.clients(plan)
        PublishPlanner.frontends(plan)

from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext


class ReleaseCleanupPlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def cleanup(plan: Plan, parameters: dict):
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Cleanup of repo",
                TaskType.RELEASE_CLEANUP,
                [repo],
                parameters=parameters
            )

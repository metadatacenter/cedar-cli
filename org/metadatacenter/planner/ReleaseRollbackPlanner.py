from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext


class ReleaseRollbackPlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def rollback(plan: Plan):
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Rollback release of repo",
                TaskType.RELEASE_ROLLBACK,
                [repo]
            )

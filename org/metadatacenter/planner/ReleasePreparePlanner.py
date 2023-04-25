from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext


class ReleasePreparePlanner(Planner):

    def __init__(self):
        super().__init__()

    def prepare(self, plan: Plan):
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo]
            )

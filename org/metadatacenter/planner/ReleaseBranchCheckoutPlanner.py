from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext


class ReleaseBranchCheckoutPlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def checkout(plan: Plan, parameters: dict):
        for repo in GlobalContext.repos.get_list_top():
            plan.add_task(
                "Check out branch",
                TaskType.RELEASE_BRANCH_CHECKOUT,
                [repo],
                parameters=parameters
            )

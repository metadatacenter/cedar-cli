from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.PreReleaseBranchType import PreReleaseBranchType
from org.metadatacenter.model.ReleasePreparePhase import ReleasePreparePhase
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.GlobalContext import GlobalContext


class ReleasePreparePlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def prepare(plan: Plan):
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo],
                parameters={
                    "branch_type": PreReleaseBranchType.RELEASE,
                    "release_prepare_phase": ReleasePreparePhase.SET_VERSIONS
                }
            )
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo],
                parameters={
                    "branch_type": PreReleaseBranchType.RELEASE,
                    "release_prepare_phase": ReleasePreparePhase.BUILD
                }
            )
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo],
                parameters={
                    "branch_type": PreReleaseBranchType.NEXT_DEV,
                    "release_prepare_phase": ReleasePreparePhase.SET_VERSIONS
                }
            )
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo],
                parameters={
                    "branch_type": PreReleaseBranchType.NEXT_DEV,
                    "release_prepare_phase": ReleasePreparePhase.BUILD
                }
            )

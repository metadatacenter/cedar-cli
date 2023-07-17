from org.metadatacenter.model.Plan import Plan
from org.metadatacenter.model.PreReleaseBranchType import PreReleaseBranchType
from org.metadatacenter.model.ReleasePreparePhase import ReleasePreparePhase
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.planner.Planner import Planner
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.GlobalContext import GlobalContext


class ReleasePreparePlanner(Planner):

    def __init__(self):
        super().__init__()

    @staticmethod
    def prepare(plan: Plan):
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE_CREATE_BRANCH,
                [repo],
                parameters={
                    Const.PARAM_BRANCH_TYPE: PreReleaseBranchType.RELEASE,
                }
            )
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo],
                parameters={
                    Const.PARAM_BRANCH_TYPE: PreReleaseBranchType.RELEASE,
                    Const.PARAM_RELEASE_PREPARE_PHASE: ReleasePreparePhase.SET_VERSIONS
                }
            )
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo],
                parameters={
                    Const.PARAM_BRANCH_TYPE: PreReleaseBranchType.RELEASE,
                    Const.PARAM_RELEASE_PREPARE_PHASE: ReleasePreparePhase.BUILD
                }
            )
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE_CREATE_BRANCH,
                [repo],
                parameters={
                    Const.PARAM_BRANCH_TYPE: PreReleaseBranchType.NEXT_DEV
                }
            )
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo],
                parameters={
                    Const.PARAM_BRANCH_TYPE: PreReleaseBranchType.NEXT_DEV,
                    Const.PARAM_RELEASE_PREPARE_PHASE: ReleasePreparePhase.SET_VERSIONS
                }
            )
        for repo in GlobalContext.repos.get_release_all():
            plan.add_task(
                "Prepare release of repo",
                TaskType.RELEASE_PREPARE,
                [repo],
                parameters={
                    Const.PARAM_BRANCH_TYPE: PreReleaseBranchType.NEXT_DEV,
                    Const.PARAM_RELEASE_PREPARE_PHASE: ReleasePreparePhase.BUILD
                }
            )

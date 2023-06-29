from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.PreReleaseBranchType import PreReleaseBranchType
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Util import Util


class ReleasePrepareCreateBranchShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def create_branch(cls, repo: Repo, branch_type: PreReleaseBranchType) -> PlanTask:
        task = PlanTask(cls.get_typed_name(branch_type), TaskType.SHELL, repo)
        task.command_list = []
        version, branch_name, tag_name = Util.get_release_vars(branch_type)

        task.command_list.extend([
            *cls.macro_create_pre_release_branch(branch_name),
        ])
        return task

    @classmethod
    def macro_create_pre_release_branch(cls, branch_name: str):
        return ('echo "Create branch"',
                '      git checkout develop',
                '      git pull origin develop',
                '      git checkout -b ' + branch_name)

    @classmethod
    def get_typed_name(cls, branch_type: PreReleaseBranchType):
        s = "Prepare branch for "
        s += " pre-release" if branch_type == PreReleaseBranchType.RELEASE else "next dev"
        return s

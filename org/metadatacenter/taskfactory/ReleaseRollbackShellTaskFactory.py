from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Util import Util


class ReleaseRollbackShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def rollback_generic(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Rollback release of angularJS project", TaskType.SHELL, repo)

        rollback_branch, rollback_tag = Util.get_rollback_vars()

        if rollback_branch.startswith('release/pre'):
            task.command_list = [
                *cls.macro_checkout_develop(),
                *cls.macro_delete_local_and_remote_branch(rollback_branch),
                *cls.macro_delete_local_and_remote_tag(rollback_tag)
            ]
            return task

    @classmethod
    def macro_checkout_develop(cls):
        return ('echo "Checking out develop"',
                '      git checkout develop')

    @classmethod
    def macro_delete_local_and_remote_branch(cls, branch: str):
        return ('echo "Delete local and remote branch"',
                '      git branch -D "' + branch + '"',
                '      git push -d origin "' + branch + '"')

    @classmethod
    def macro_delete_local_and_remote_tag(cls, tag: str):
        return ('echo "Delete local and remote tag"',
                '      git tag -d "' + tag + '"',
                '      git push -d origin "' + tag + '"')

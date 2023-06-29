from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType
from org.metadatacenter.util.Util import Util


class ReleaseRollbackShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def rollback_generic(cls, repo: Repo) -> PlanTask:
        task = PlanTask("Rollback release of generic repo", TaskType.SHELL, repo)

        rollback_branch, rollback_tag = Util.get_rollback_vars()

        is_delete_branch = False
        is_delete_tag = False

        if rollback_branch.startswith('release/pre'):
            is_delete_branch = True
        if rollback_branch.startswith('release/post'):
            is_delete_branch = True
        if rollback_tag.startswith('release-'):
            is_delete_tag = True

        if not is_delete_branch and not is_delete_tag:
            task = PlanTask("Rollback not supported", TaskType.SHELL, repo)
            task.command_list = []
        else:
            task.command_list = [
                *cls.macro_checkout_develop(),
                *(cls.macro_delete_local_and_remote_branch(rollback_branch) if is_delete_branch else []),
                *(cls.macro_delete_local_and_remote_tag(rollback_tag) if is_delete_tag else [])
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
                '      git push origin --delete "' + branch + '"')

    @classmethod
    def macro_delete_local_and_remote_tag(cls, tag: str):
        return ('echo "Delete local and remote tag"',
                '      git tag -d "' + tag + '"',
                '      git push -d origin "' + tag + '"')

    @classmethod
    def macro_delete_local_branch(cls, branch: str):
        return ('echo "Delete local and remote branch"',
                '      git branch -D "' + branch + '"')

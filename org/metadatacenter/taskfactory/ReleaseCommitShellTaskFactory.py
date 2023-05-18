from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.TaskType import TaskType


class ReleaseCommitShellTaskFactory:

    def __init__(self):
        super().__init__()

    @classmethod
    def commit_generic(cls, repo: Repo, parent_task: PlanTask) -> PlanTask:
        task = PlanTask("Commiting generic repo", TaskType.SHELL, repo)
        task.command_list = []
        task.parameters = parent_task.parameters

        release_version = task.parameters['release_version']
        release_tag = task.parameters['tag']
        next_dev_version = task.parameters['next_dev_version']
        post_branch = task.parameters['post_branch']

        task.command_list.extend([
            'git checkout main',
            'git pull',
            'git merge -X theirs --no-ff -m "Updating main to release ' + release_version + '" "' + release_tag + '"',
            'git push',
            'git checkout develop',
            'git pull',
            'git merge -X theirs --no-ff -m "Updating develop to next snapshot ' + next_dev_version + '" "' + post_branch + '"',
            'git push',
        ])
        return task

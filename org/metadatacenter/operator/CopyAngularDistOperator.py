from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask
from org.metadatacenter.operator.Operator import Operator
from org.metadatacenter.taskfactory.ShellTaskFactory import ShellTaskFactory
from org.metadatacenter.util.Util import Util

console = Console()


class CopyAngularDistOperator(Operator):

    def __init__(self):
        super().__init__()

    def expand(self, task: PlanTask):
        source_path = Util.get_wd(task.repo) + '/dist/' + task.repo.name
        target_repo = task.variables['target_repo']
        target_path = Util.get_wd(target_repo)
        task.add_task_as_task(ShellTaskFactory.copy_src_content_to_dest(source_path, target_path, task.repo))

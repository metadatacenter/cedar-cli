from abc import ABC

from rich.console import Console

from org.metadatacenter.model.PlanTask import PlanTask

console = Console()


class Operator(ABC):

    def __init__(self):
        super().__init__()

    def expand_task(self, task: PlanTask):
        from org.metadatacenter.util.GlobalContext import GlobalContext
        task_type = task.task_type
        type_operator = GlobalContext.get_task_operator(task_type)
        if type_operator is not None:
            type_operator.expand(task)
        else:
            # TODO: insert a NOP
            pass

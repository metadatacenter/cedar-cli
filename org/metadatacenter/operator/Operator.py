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
        # self.handle_post_tasks(task)

    # def handle_post_tasks(self, task: PlanTask):
    #     from org.metadatacenter.util.GlobalContext import GlobalContext
    #     global_task_type = GlobalContext.task_type
    #     console.print("1 Operator.handle_post_tasks global_task_type=" + global_task_type)
    #     if global_task_type in task.repo.post_tasks:
    #         post_tasks = task.repo.post_tasks[global_task_type]
    #         for post_task in post_tasks:
    #             console.print("Operator.handle_post_tasks " +  post_task.parent_task_type + "vs" +  task.task_type)
    #             if post_task.parent_task_type == task.task_type:
    #                 from org.metadatacenter.model.PlanTask import PlanTask
    #                 clone_repo = copy.deepcopy(task.repo)
    #                 clone_repo.post_tasks = []
    #                 post_task_obj = PlanTask("Execute post task", post_task.task_type, clone_repo)
    #                 post_task_obj.variables = post_task.variables
    #                 task.add_task_as_task(post_task_obj)

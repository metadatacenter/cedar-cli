from org.metadatacenter.model.WorkerType import WorkerType


class Task:

    def __init__(self, worker_type: WorkerType, repo_list, title: str, progress_text: str, parameters=None):
        if parameters is None:
            parameters = dict()
        self.worker_type = worker_type
        self.repo_list = repo_list
        self.title = title
        self.progress_text = progress_text
        self.parameters = parameters

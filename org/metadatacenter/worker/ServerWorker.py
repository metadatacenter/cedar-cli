from rich.console import Console

from org.metadatacenter.worker.Worker import Worker

console = Console()


class ServerWorker(Worker):
    def __init__(self):
        super().__init__()

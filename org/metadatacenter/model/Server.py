from org.metadatacenter.model.CheckRunning import CheckRunning
from org.metadatacenter.model.ServerTag import ServerTag


class Server:

    def __init__(self, name: str,
                 tag: ServerTag = None,
                 port: int = None,
                 admin_port: int = None,
                 check_running: CheckRunning = None,
                 display_name=None,
                 response_path=""
                 ) -> None:
        self.name = name
        self.tag = tag
        self.port = port
        self.admin_port = admin_port
        self.check_running = check_running
        self.display_name = display_name
        self.response_path = response_path

from org.metadatacenter.model.CheckRunning import CheckRunning
from org.metadatacenter.model.ServerTag import ServerTag
from org.metadatacenter.model.ServerType import ServerType


class Server:

    def __init__(self, name: str,
                 server_type: ServerType = None,
                 tag: ServerTag = None,
                 port: int = None,
                 admin_port: int = None,
                 stop_port: int = None,
                 check_running: CheckRunning = None,
                 check_response: str = None,
                 display_name=None
                 ) -> None:
        self.name = name
        self.server_type = server_type
        self.tag = tag
        self.port = port
        self.admin_port = admin_port
        self.stop_port = stop_port
        self.check_running = check_running
        self.check_response = check_response
        self.display_name = display_name

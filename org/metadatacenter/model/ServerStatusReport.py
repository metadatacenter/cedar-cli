from org.metadatacenter.model.Server import Server
from org.metadatacenter.model.ServerStatus import ServerStatus


class ServerStatusReport:

    def __init__(self, server: Server) -> None:
        self.server = server
        self.status = ServerStatus.UNKNOWN
        self.exception = None
        self.status_code = None

    def add_exception(self, exception):
        self.exception = exception
        self.status = ServerStatus.ERROR

    def set_status_code(self, status_code: int):
        self.status_code = status_code
        if status_code == 200:
            self.status = ServerStatus.OK
        elif status_code == 301: #nginx
            self.status = ServerStatus.OK
        else:
            self.status = ServerStatus.NOT_RUNNING

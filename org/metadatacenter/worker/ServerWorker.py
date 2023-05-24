import socket
from contextlib import closing

import requests
from rich.console import Console
from rich.table import Table

from org.metadatacenter.model.CheckRunning import CheckRunning
from org.metadatacenter.model.Server import Server
from org.metadatacenter.model.ServerStatus import ServerStatus
from org.metadatacenter.model.ServerStatusReport import ServerStatusReport
from org.metadatacenter.model.ServerTag import ServerTag
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class ServerWorker(Worker):
    def __init__(self):
        super().__init__()

    @staticmethod
    def status():
        server_status_map = {}
        ServerWorker.check_status_of(ServerTag.MICROSERVICE, server_status_map)
        ServerWorker.check_status_of(ServerTag.INFRASTRUCTURE, server_status_map)
        ServerWorker.check_status_of(ServerTag.FRONTEND, server_status_map)
        ServerWorker.check_status_of(ServerTag.DASHBOARD, server_status_map)
        ServerWorker.check_status_of(ServerTag.FRONTEND_NON_ESSENTIAL, server_status_map)
        table = Table("Server", "Status", "Port", 'Error', title="CEDAR Server status list")
        prev_server_tag = None
        for server in Util.get_servers():
            status = "❓"
            display_name = server.name
            error = ''
            if server.display_name is not None:
                display_name = server.display_name
            if server.name in server_status_map:
                if server_status_map[server.name].status == ServerStatus.OK:
                    status = "✅"
                if server_status_map[server.name].status == ServerStatus.NOT_RUNNING:
                    status = "❌"
                if server_status_map[server.name].status == ServerStatus.ERROR:
                    status = "❌ ❌"
                if server_status_map[server.name].exception is not None:
                    error = server_status_map[server.name].exception
            current_server_tag = server.tag
            if prev_server_tag is not None and prev_server_tag != current_server_tag:
                table.add_section()
                table.add_row("[bold magenta]" + server.tag.capitalize(), '', '', '')

            prev_server_tag = current_server_tag
            # if error != '':
            #     table.add_section()
            table.add_row(display_name, status, str(server.port), error)
            # if error != '':
            #     table.add_section()
        console.print(table)

    @staticmethod
    def check_status_of(tag: ServerTag, server_status_map: dict):
        for server in Util.get_servers():
            if server.tag == tag:
                ServerWorker.check_status_of_server(server, server_status_map)

    @staticmethod
    def check_status_of_server(server: Server, server_status_map: dict):
        # console.log('----------------------------------------------------------------')
        # console.log(server.name)
        # console.log(server.check_running)
        if server.check_running == CheckRunning.HEALTH_CHECK:
            ServerWorker.check_status_by_health_check(server, server_status_map)
        elif server.check_running == CheckRunning.RESPONSE:
            ServerWorker.check_status_by_response(server, server_status_map)
        elif server.check_running == CheckRunning.OPEN_PORT:
            ServerWorker.check_status_by_open_port(server, server_status_map)

    @staticmethod
    def check_status_by_health_check(server: Server, server_status_map: dict):
        server_status_report = ServerStatusReport(server)
        port_open = ServerWorker.is_port_open('localhost', server.port)
        if not port_open:
            server_status_report.status = ServerStatus.NOT_RUNNING
            server_status_report.exception = "Port not open"
        else:
            url = 'http://localhost:' + str(server.admin_port) + '/healthcheck'
            try:
                response = requests.head(url)
                server_status_report.set_status_code(response.status_code)
            except Exception as e:
                server_status_report.add_exception(str(e))
        server_status_map[server.name] = server_status_report

    @staticmethod
    def check_status_by_response(server: Server, server_status_map: dict):
        server_status_report = ServerStatusReport(server)
        port_open = ServerWorker.is_port_open('localhost', server.port)
        if not port_open:
            server_status_report.status = ServerStatus.NOT_RUNNING
            server_status_report.exception = "Port not open"
        else:
            url = 'http://localhost:' + str(server.port)
            try:
                response = requests.head(url)
                server_status_report.set_status_code(response.status_code)
            except Exception as e:
                server_status_report.add_exception(str(e))
        server_status_map[server.name] = server_status_report

    @staticmethod
    def check_status_by_open_port(server: Server, server_status_map: dict):
        server_status_report = ServerStatusReport(server)
        port_open = ServerWorker.is_port_open('localhost', server.port)
        if port_open:
            server_status_report.status = ServerStatus.OK
        else:
            server_status_report.status = ServerStatus.NOT_RUNNING
            server_status_report.exception = "Port not open"
        server_status_map[server.name] = server_status_report

    @staticmethod
    def is_port_open(host: str, port: int):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            if sock.connect_ex((host, port)) == 0:
                return True
            else:
                return False

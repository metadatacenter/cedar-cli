import csv
import os
import socket
from contextlib import closing

import requests
from rich.console import Console
from rich import box
from rich.table import Table
from rich.text import Text

from org.metadatacenter.model.CheckRunning import CheckRunning
from org.metadatacenter.model.Server import Server
from org.metadatacenter.model.ServerStatus import ServerStatus
from org.metadatacenter.model.ServerStatusReport import ServerStatusReport
from org.metadatacenter.model.ServerTag import ServerTag
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


# The frontends whose STALE means an Embeddable Editor other than the one their lock names, not an
# old jar, and the checkout each reinstalls in. Mirrors serves_cee and fe_dir in cedar-services.sh.
EDITOR_FRONTENDS = {
    "ui-main": "cedar-template-editor",
    "ui-workspace": "cedar-workspace",
}


class ServerWorker(Worker):
    NATIVE_MICROSERVICES = {
        "group", "messaging", "repo", "resource", "schema", "artifact",
        "terminology", "user", "valuerecommender", "submission", "worker",
        "openview", "monitor", "impex", "bridge",
    }

    def __init__(self):
        super().__init__()

    @staticmethod
    def status(native_status_lines):
        native_rows = ServerWorker.parse_native_status(native_status_lines)
        server_status_map = {}
        ServerWorker.check_status_of(ServerTag.INFRASTRUCTURE, server_status_map)

        table = Table(
            "Service", "PID", "Port", "Health", "Binary", "Log errors",
            title="CEDAR native status", box=box.SIMPLE_HEAVY,
            header_style="bold", show_edge=False, pad_edge=False)
        table.columns[1].justify = "right"
        table.columns[2].justify = "right"
        table.columns[5].justify = "right"

        microservices = [
            row for row in native_rows
            if row["service"] in ServerWorker.NATIVE_MICROSERVICES]
        frontends = [
            row for row in native_rows
            if row["service"] not in ServerWorker.NATIVE_MICROSERVICES]
        ServerWorker.add_native_section(table, "Microservices", microservices)

        infrastructure = [
            server for server in Util.get_servers()
            if server.tag == ServerTag.INFRASTRUCTURE]
        ServerWorker.add_host_section(
            table, "Infrastructure", infrastructure, server_status_map)
        ServerWorker.add_native_section(table, "Frontends", frontends)

        console.print(table)
        ServerWorker.print_summary(native_rows, infrastructure, server_status_map)

    @staticmethod
    def parse_native_status(lines):
        reader = csv.DictReader(lines, delimiter="\t")
        required = {
            "service", "pid", "port", "listener", "health", "binary",
            "log_errors",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("native status output has an unexpected schema")
        return list(reader)

    @staticmethod
    def add_category(table, label):
        if table.row_count:
            table.add_section()
        table.add_row(Text(label, style="bold magenta"), "", "", "", "", "")

    @staticmethod
    def add_native_section(table, label, rows):
        if not rows:
            return
        ServerWorker.add_category(table, label)
        for row in rows:
            pid = row["pid"]
            errors = row["log_errors"]
            if errors.isdigit():
                errors = f"{int(errors):,}"
            table.add_row(
                row["service"],
                ServerWorker.style_pid(pid),
                ServerWorker.style_port(row["port"], row["listener"]),
                ServerWorker.style_health(row["health"]),
                ServerWorker.style_binary(row["binary"]),
                ServerWorker.style_errors(errors),
            )

    @staticmethod
    def add_host_section(table, label, servers, server_status_map):
        if not servers:
            return
        ServerWorker.add_category(table, label)
        for server in servers:
            report = server_status_map[server.name]
            if report.exception == "Port not open":
                listener = "down"
            elif report.status == ServerStatus.UNKNOWN:
                listener = "unknown"
            else:
                listener = "up"
            if report.status == ServerStatus.OK:
                health = "healthy"
            elif report.status == ServerStatus.ERROR:
                health = "error"
            elif report.status == ServerStatus.NOT_RUNNING:
                health = "down"
            else:
                health = "unknown"
            display_name = server.display_name or server.name
            table.add_row(
                display_name, Text("—", style="dim"),
                ServerWorker.style_port(str(server.port), listener),
                ServerWorker.style_health(health), Text("—", style="dim"),
                Text("—", style="dim"))

    @staticmethod
    def style_pid(value):
        if value.startswith("!"):
            return Text(value, style="bold red")
        if value.startswith("~"):
            return Text(value, style="yellow")
        if value == "docker":
            return Text(value, style="cyan")
        if value == "-":
            return Text("—", style="dim")
        return Text(value, style="cyan")

    @staticmethod
    def style_port(port, listener):
        styles = {"up": "green", "internal": "cyan", "down": "red", "unknown": "yellow"}
        return Text(f"{port} {listener}", style=styles.get(listener, "yellow"))

    @staticmethod
    def style_health(value):
        styles = {
            "healthy": "green", "docker": "cyan", "starting": "yellow",
            "UNHEALTHY": "bold red", "error": "bold red", "down": "red",
            "unknown": "yellow",
        }
        return Text(value, style=styles.get(value, "yellow"))

    @staticmethod
    def style_binary(value):
        if value == "STALE":
            return Text(value, style="bold red")
        if value == "current":
            return Text(value, style="green")
        return Text("—", style="dim")

    @staticmethod
    def style_errors(value):
        if value == "-":
            return Text("—", style="dim")
        try:
            style = "dim" if int(value.replace(",", "")) == 0 else "yellow"
        except ValueError:
            style = "yellow"
        return Text(value, style=style)

    @staticmethod
    def print_summary(native_rows, infrastructure, server_status_map):
        docker_rows = [row for row in native_rows if row["health"] == "docker"]
        native_rows_only = [row for row in native_rows if row["health"] != "docker"]
        healthy = sum(row["health"] == "healthy" for row in native_rows_only)
        infra_healthy = sum(
            server_status_map[server.name].status == ServerStatus.OK
            for server in infrastructure)
        summary = Text("Summary  ", style="bold")
        summary.append(f"native {healthy}/{len(native_rows_only)} healthy")
        summary.append(f"  •  infrastructure {infra_healthy}/{len(infrastructure)} available")
        console.print(summary)

        warnings = []
        stale = [row["service"] for row in native_rows if row["binary"] == "STALE"]
        unmanaged = [row["service"] for row in native_rows if row["pid"].startswith("~")]
        foreign = [row["service"] for row in native_rows if row["pid"].startswith("!")]
        unhealthy = [
            f'{row["service"]} ({row["health"]})' for row in native_rows
            if row["health"] not in {"healthy", "docker"}]
        stale_jars = [service for service in stale if service not in EDITOR_FRONTENDS]
        if stale_jars:
            warnings.append(f"stale binaries: {', '.join(stale_jars)}; restart them")
        for service in stale:
            if service in EDITOR_FRONTENDS:
                warnings.append(
                    f"{service} serves an Embeddable Editor other than the one its lock names; run "
                    f"(cd $CEDAR_HOME/{EDITOR_FRONTENDS[service]} && npm ci && npx gulp copy:cee)")
        if unmanaged:
            warnings.append(f"unmanaged CEDAR processes: {', '.join(unmanaged)}; restart adopts them")
        if foreign:
            warnings.append(f"foreign listeners: {', '.join(foreign)}; native control will not touch them")
        if unhealthy:
            warnings.append(f"not healthy: {', '.join(unhealthy)}")
        if docker_rows:
            warnings.append(
                "container-owned: "
                f"{', '.join(row['service'] for row in docker_rows)}; use cedarcli docker status")
        for warning in warnings:
            console.print(Text(f"WARNING  {warning}", style="yellow"))

        cedar_host = os.environ.get("CEDAR_HOST")
        if cedar_host:
            console.print(Text(
                f"Login    https://cedar.{cedar_host} once frontend, resource, and user are healthy",
                style="dim"))

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

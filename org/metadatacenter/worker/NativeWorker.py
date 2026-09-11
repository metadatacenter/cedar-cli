from org.metadatacenter.util.InvocationContext import invocation_environment
import os
import re
import shlex
import sys
from typing import Iterable

from rich.console import Console

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.ServerWorker import ServerWorker
from org.metadatacenter.worker.Worker import Worker


console = Console()


class NativeWorker(Worker):
    """Run the native application tier without owning a terminal application."""

    MICROSERVICES = (
        "group", "messaging", "repo", "resource", "schema", "artifact",
        "terminology", "user", "valuerecommender", "submission", "worker",
        "openview", "monitor", "impex", "bridge",
    )
    FRONTENDS = (
        "ui-main", "ui-workspace", "ui-designer", "ui-openview", "ui-content",
        "ui-monitoring", "ui-bridging",
    )

    @staticmethod
    def controller_path() -> str:
        cedar_home = Util.cedar_home or invocation_environment()["CEDAR_HOME"]
        return os.path.join(cedar_home, "cedar-development", "ops", "cedar-services.sh")

    @classmethod
    def execute(cls, action: str, services: Iterable[str] = (), title: str = None,
                show_command: bool = True, echo_streams: bool = True,
                show_title: bool = True):
        arguments = [cls.controller_path(), action, *services]
        command = " ".join(shlex.quote(argument) for argument in arguments)
        return Worker.execute_generic_shell_commands(
            [command], title=title or f"Native CEDAR: {action}",
            show_command=show_command, echo_streams=echo_streams,
            show_title=show_title)

    @classmethod
    def infrastructure_ports(cls) -> dict:
        """The infrastructure the controller manages, read from the controller's own list.

        Duplicating the names here would let the two drift, and the drift would show up as an
        infrastructure service nobody notices is missing.
        """
        try:
            text = open(cls.controller_path(), encoding="utf-8").read()
        except OSError:
            return {}
        match = re.search(r"^INFRASTRUCTURE_PORTS=\((.*?)^\)", text, re.MULTILINE | re.DOTALL)
        if not match:
            return {}
        ports = {}
        for line in match.group(1).splitlines():
            entry = line.strip().strip('"')
            if not entry or entry.startswith("#"):
                continue
            name, _, port = entry.partition(" ")
            if name and port:
                ports[name] = port.strip()
        return ports

    @classmethod
    def infrastructure_gaps(cls):
        """Infrastructure ports with no listener, or None when the controller cannot say.

        An empty list means every managed port is already served, which is the state in which
        starting infrastructure again is not a no-op but a failure: Keycloak refuses a bound
        8080 and takes the whole start with it.
        """
        expected = cls.infrastructure_ports()
        if not expected:
            return None
        result = cls.execute(
            "running-infra", title="Reading native infrastructure",
            show_command=False, echo_streams=False, show_title=False)
        if result.returncode:
            return None
        listening = {line.split(" (", 1)[0].strip() for line in result if line.strip()}
        return [name for name in expected if name not in listening]

    @classmethod
    def start(cls, services: Iterable[str] = ()):
        return cls.execute("start", services, "Starting native CEDAR services")

    @classmethod
    def stop(cls, services: Iterable[str] = ()):
        return cls.execute("stop", services, "Stopping native CEDAR services")

    @classmethod
    def restart(cls, services: Iterable[str] = ()):
        return cls.execute("restart", services, "Restarting native CEDAR services")

    @classmethod
    def status(cls):
        result = cls.execute(
            "status-tsv", title="Native CEDAR process status",
            show_command=False, echo_streams=False, show_title=False)
        if result.returncode:
            for line in result:
                print(line)
            return result
        ServerWorker.status(result)
        return result

    @classmethod
    def health(cls):
        return cls.execute("health", title="Checking native CEDAR health")

    @classmethod
    def watch(cls):
        return cls.execute("watch", title="Watching native CEDAR process status")

    @classmethod
    def logs(cls, service: str, lines: int = 100, dropwizard: bool = False):
        """Hand the terminal to tail rather than relaying its output.

        Every other action here is relayed line by line through rich, which rewraps each line to
        the terminal width, strips the indentation a stack trace is made of, and keeps the whole
        stream in memory. A follow runs until it is interrupted and prints exactly those lines, so
        it replaces this process instead: the log arrives as it was written, nothing accumulates,
        the interrupt reaches tail directly, and tail's own status becomes the CLI's.
        """
        controller = cls.controller_path()
        arguments = [controller, "logs", service, "--lines", str(lines)]
        if dropwizard:
            arguments.append("--dropwizard")
        appender = "Dropwizard log" if dropwizard else "log"
        console.print(f"[yellow]Following native CEDAR {appender}: {service}[/yellow]")
        sys.stdout.flush()
        os.execve(controller, arguments, invocation_environment())

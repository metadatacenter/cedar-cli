import os
import shlex
from typing import Iterable

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.ServerWorker import ServerWorker
from org.metadatacenter.worker.Worker import Worker


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
        cedar_home = Util.cedar_home or os.environ["CEDAR_HOME"]
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
    def logs(cls, service: str):
        return cls.execute("logs", (service,), f"Following native CEDAR log: {service}")

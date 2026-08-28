import os
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.style import Style
from rich.table import Table
from rich.text import Text

from org.metadatacenter.model.CedarMode import CedarMode
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.ModeManager import ModeError, ModeManager
from org.metadatacenter.worker.Worker import Worker

console = Console()

release_list = [
    Const.CEDAR_HOME,
    Const.CEDAR_HOST,
    Const.CEDAR_VERSION,
    Const.CEDAR_RELEASE_VERSION,
    Const.CEDAR_NEXT_DEVELOPMENT_VERSION,
]

CEDAR_ENV_PREFIX = 'CEDAR_'
SENSITIVE_NAME_PARTS = (
    'PASSWORD',
    'SECRET',
    'TOKEN',
    'API_KEY',
    'PRIVATE_KEY',
    'CREDENTIAL',
)


class EnvWorker(Worker):
    def __init__(self):
        super().__init__()

    @staticmethod
    def _allowed_surfaces(mode: CedarMode):
        return {
            CedarMode.NATIVE: ('native',),
            CedarMode.HYBRID: ('native', 'docker'),
            CedarMode.DOCKER: ('docker',),
        }[mode]

    @staticmethod
    def _effective_environment(surface: Optional[str] = None):
        mode = ModeManager.current()
        if mode is None:
            raise ModeError(
                "CEDAR mode is not set; run cedarcli mode native|hybrid|docker first"
            )
        allowed = EnvWorker._allowed_surfaces(mode)
        if surface is None:
            if len(allowed) > 1:
                raise ModeError(
                    "CEDAR mode is hybrid and has separate native and Docker environments; "
                    "choose native or docker"
                )
            surface = allowed[0]
        if surface not in allowed:
            raise ModeError(
                f"CEDAR mode is {mode.value}; the {surface} environment is not active"
            )
        return surface, ModeManager.profile_environment(surface, mode)

    @staticmethod
    def _is_sensitive(name: str):
        upper_name = name.upper()
        return any(part in upper_name for part in SENSITIVE_NAME_PARTS)

    @staticmethod
    def _display_value(name: str, value: str):
        return Text("<redacted>" if EnvWorker._is_sensitive(name) else value)

    @staticmethod
    def _list(environment, title, filter_term=None):
        variables = [
            (name, value)
            for name, value in sorted(environment.items())
            if name.startswith(CEDAR_ENV_PREFIX)
            and (filter_term is None or filter_term.lower() in name.lower())
        ]
        table = Table("Name", "Value", title=title)
        for name, value in variables:
            table.add_row(Text(name), EnvWorker._display_value(name, value))
        table.caption = f"{len(variables)} variables; sensitive values are redacted"
        table.style = Style(color="green")
        console.print(table)

    @staticmethod
    def list(surface: Optional[str] = None):
        surface, environment = EnvWorker._effective_environment(surface)
        EnvWorker._list(
            environment,
            f"CEDAR {surface} environment variables",
        )

    @staticmethod
    def status():
        mode = ModeManager.current()
        table = Table("Setting", "Value", title="CEDAR environment status")
        table.add_row("Mode", mode.value if mode else "not set")
        table.add_row("Mode state", str(ModeManager.state_path()))
        if mode is not None:
            for surface in EnvWorker._allowed_surfaces(mode):
                environment = ModeManager.profile_environment(surface, mode)
                profile = (
                    ModeManager.NATIVE_PROFILE
                    if surface == 'native'
                    else ModeManager.DOCKER_PROFILE
                )
                table.add_row(
                    f"{surface.title()} profile",
                    str(ModeManager.cedar_home() / Path(profile)),
                )
                table.add_row(
                    f"{surface.title()} host",
                    environment.get(Const.CEDAR_HOST, "not set"),
                )
                gateway = environment.get(Const.CEDAR_NET_GATEWAY)
                subnet = environment.get(Const.CEDAR_NET_SUBNET)
                network = "; ".join(filter(None, (
                    f"gateway={gateway}" if gateway else None,
                    f"subnet={subnet}" if subnet else None,
                )))
                table.add_row(f"{surface.title()} network", network or "not set")
                if surface == 'docker':
                    table.add_row(
                        "Docker topology",
                        environment.get("CEDAR_DOCKER_MODE", "not set"),
                    )
                    table.add_row(
                        "Runtime images",
                        environment.get("CEDAR_IMAGE_PREFIX", "not set"),
                    )
                    table.add_row(
                        "Java base images",
                        environment.get("CEDAR_BASE_IMAGE_PREFIX", "not set"),
                    )
                    from org.metadatacenter.worker.DockerWorker import DockerWorker
                    table.add_row(
                        "Active image set",
                        DockerWorker.active_train() or "not recorded",
                    )
        table.style = Style(color="green")
        console.print(table)

    @staticmethod
    def release():
        mode = ModeManager.current()
        environment = os.environ
        if mode is not None:
            surface = 'docker' if mode is CedarMode.DOCKER else 'native'
            environment = ModeManager.profile_environment(surface, mode)
        table = Table("Name", "Value", title="CEDAR release environment variables")
        EnvWorker.list_specific_vars(table, release_list, environment)

    @staticmethod
    def list_specific_vars(table: Table, var_names: List[str], environment=None):
        present_cnt = 0
        missing_cnt = 0
        var_map = {}
        source_environment = os.environ if environment is None else environment
        for name, value in sorted(source_environment.items()):
            if name.startswith(CEDAR_ENV_PREFIX):
                var_map[name] = value
        for name in var_names:
            if name in var_map:
                table.add_row(
                    Text(name, style="yellow"),
                    Text("✅ " + var_map[name], style="green"),
                )
                present_cnt += 1
            else:
                table.add_row(Text(name, style="yellow"), Text('❌ MISSING', style="red"))
                missing_cnt += 1

        caption = str(present_cnt) + " variables present"
        if missing_cnt > 0:
            caption += ", [red]" + str(missing_cnt) + " missing"
        table.caption = caption
        table.style = Style(color="green")
        console.print(table)

    @staticmethod
    def filter(filter_term: str, surface: Optional[str] = None):
        surface, environment = EnvWorker._effective_environment(surface)
        EnvWorker._list(
            environment,
            f"CEDAR {surface} environment variables matching {filter_term}",
            filter_term,
        )

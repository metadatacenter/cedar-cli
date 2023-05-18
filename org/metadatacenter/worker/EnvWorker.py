import os

from rich.console import Console
from rich.style import Style
from rich.table import Table

from org.metadatacenter.util.Const import Const
from org.metadatacenter.worker.Worker import Worker

console = Console()

core_list = [
    Const.CEDAR_HOME,
    'CEDAR_DOCKER_HOME',
    'CEDAR_HOST',
    Const.CEDAR_VERSION,
    Const.CEDAR_RELEASE_VERSION,
    Const.CEDAR_NEXT_DEVELOPMENT_VERSION,
    'CEDAR_FRONTEND_TARGET',
    'CEDAR_NET_GATEWAY',
    'CEDAR_NET_SUBNET'
]

CEDAR_ENV_PREFIX = 'CEDAR_'


class EnvWorker(Worker):
    def __init__(self):
        super().__init__()

    def list(self):
        cnt = 0
        table = Table("Name", "Value", title="CEDAR environment variables")
        for name, value in os.environ.items():
            if name.startswith(CEDAR_ENV_PREFIX):
                table.add_row(name, value)
                cnt += 1
        table.caption = str(cnt) + " variables"
        table.style = Style(color="green")
        console.print(table)

    def core(self):
        table = Table("Name", "Value", title="CEDAR core environment variables")
        present_cnt = 0
        missing_cnt = 0
        core_map = {}
        for name, value in os.environ.items():
            if name.startswith(CEDAR_ENV_PREFIX):
                core_map[name] = value
        for name in core_list:
            if name in core_map:
                table.add_row("[yellow]" + name, "✅ [green]" + core_map[name])
                present_cnt += 1
            else:
                table.add_row("[yellow]" + name, '❌ [red]MISSING')
                missing_cnt += 1

        caption = str(present_cnt) + " variables present"
        if missing_cnt > 0:
            caption += ", [red]" + str(missing_cnt) + " missing"
        table.caption = caption
        table.style = Style(color="green")
        console.print(table)

import os
from typing import List

from rich.console import Console
from rich.style import Style
from rich.table import Table

from org.metadatacenter.util.Const import Const
from org.metadatacenter.worker.Worker import Worker

console = Console()

core_list = [
    Const.CEDAR_HOME,
    Const.CEDAR_HOST,
    Const.CEDAR_VERSION,
    Const.CEDAR_FRONTEND_TARGET,
    Const.CEDAR_NET_GATEWAY,
    Const.CEDAR_NET_SUBNET
]

release_list = [
    Const.CEDAR_HOME,
    Const.CEDAR_HOST,
    Const.CEDAR_VERSION,
    Const.CEDAR_RELEASE_VERSION,
    Const.CEDAR_NEXT_DEVELOPMENT_VERSION,
]

CEDAR_ENV_PREFIX = 'CEDAR_'


class EnvWorker(Worker):
    def __init__(self):
        super().__init__()

    @staticmethod
    def list():
        cnt = 0
        table = Table("Name", "Value", title="CEDAR environment variables")
        for name, value in sorted(os.environ.items()):
            if name.startswith(CEDAR_ENV_PREFIX):
                table.add_row(name, value)
                cnt += 1
        table.caption = str(cnt) + " variables"
        table.style = Style(color="green")
        console.print(table)

    @staticmethod
    def core():
        table = Table("Name", "Value", title="CEDAR core environment variables")
        EnvWorker.list_specific_vars(table, core_list)

    @staticmethod
    def release():
        table = Table("Name", "Value", title="CEDAR release environment variables")
        EnvWorker.list_specific_vars(table, release_list)

    @staticmethod
    def list_specific_vars(table: Table, var_names: List[str]):
        present_cnt = 0
        missing_cnt = 0
        var_map = {}
        for name, value in sorted(os.environ.items()):
            if name.startswith(CEDAR_ENV_PREFIX):
                var_map[name] = value
        for name in var_names:
            if name in var_map:
                table.add_row("[yellow]" + name, "✅ [green]" + var_map[name])
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

    @staticmethod
    def filter(filter_term: str):
        cnt = 0
        table = Table("Name", "Value", title="CEDAR environment variables")
        for name, value in sorted(os.environ.items()):
            if name.startswith(CEDAR_ENV_PREFIX) and filter_term.lower() in name.lower():
                table.add_row(name, value)
                cnt += 1
        table.caption = str(cnt) + " variables"
        table.style = Style(color="green")
        console.print(table)

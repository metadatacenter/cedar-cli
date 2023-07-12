from rich.console import Console

console = Console()


class CedarCliSettings(object):
    do_fail_on_error = True
    shell_path = '/bin/bash'
    sed_replace_in_place = "sed -i ''"

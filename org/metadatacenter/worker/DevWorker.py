import binascii
import hashlib
import os
import re
import shlex
from pathlib import Path

from rich.console import Console
from rich.style import Style
from rich.table import Table

from org.metadatacenter.config.SubdomainsFactory import SubdomainsFactory
from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class DevError(ValueError):
    pass


class DevWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def create_directories():
        cedar_home = Path(Util.cedar_home)
        relative_paths = [
            'cache/terminology', 'CEDAR_CA', 'export', 'tmp', 'log/run',
            'log/frontend-bridging', 'log/frontend-cedar', 'log/frontend-content',
            'log/frontend-cee-demo-angular', 'log/frontend-cee-demo-angular-dist',
            'log/frontend-monitoring', 'log/frontend-openview', 'log/frontend-shared',
            'log/frontend-workspace', 'log/frontend-designer',
            'log/server-artifact', 'log/server-auth', 'log/server-bridge', 'log/server-group',
            'log/server-impex', 'log/server-messaging', 'log/server-monitor', 'log/server-open',
            'log/server-repo', 'log/server-resource', 'log/server-schema',
            'log/server-submission', 'log/server-terminology', 'log/server-user',
            'log/server-valuerecommender', 'log/server-worker', 'log/cadsr-tools', 'log/nginx',
        ]
        for relative_path in relative_paths:
            (cedar_home / relative_path).mkdir(parents=True, exist_ok=True)
        console.print(f"[green]CEDAR working directories are ready under {cedar_home}.[/green]")
        return 0

    @staticmethod
    def add_hosts():
        domain = os.environ.get(Const.CEDAR_HOST)
        if not domain:
            raise DevError("CEDAR_HOST is not set. Load a CEDAR profile before adding hostnames.")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
            raise DevError(f"CEDAR_HOST is not a valid hostname suffix: {domain}")
        host_lines = "\n".join(
            f'    "{name}"'
            for name in SubdomainsFactory.build_subdomains().map
            if name
        )
        command = """
CEDAR_HOSTS=(
__CEDAR_HOST_LINES__
)

hosts=()
echo "Testing the list of CEDAR hosts:"
for i in "${CEDAR_HOSTS[@]}"
do
  HOST="$i.${CEDAR_HOST}"
  if ! ping -c 1 "$HOST" > /dev/null 2>&1
  then
    echo "Host unknown : $HOST"
    hosts+=("$HOST")
  else
    echo "Host known   : $HOST"
  fi
done

echo

if [[ ${#hosts[@]} == 0 ]];
then
  echo "All CEDAR hosts are known, nothing to do"
else
  echo "Some CEDAR hosts are unknown, we will prompt for your password in order to make modifications to /etc/hosts !"
  echo
  block=$(mktemp)
  trap 'rm -f "$block"' EXIT
  {
    printf "\n# Added by CEDAR install process on %s from here:\n" "$(date +%Y-%m-%d)"
    for i in "${hosts[@]}"; do
      echo "Host unknown, adding to /etc/hosts: $i" >&2
      printf "127.0.0.1\t%s\n" "$i"
    done
    printf "# Added by CEDAR install process until here.\n"
  } > "$block"
  sudo tee -a /etc/hosts < "$block" > /dev/null || exit 1
fi

echo
"""
        return Worker.execute_generic_shell_commands([
            command.replace("__CEDAR_HOST_LINES__", host_lines)
        ],
            title="Adding CEDAR hostnames to /etc/hosts",
        )

    @staticmethod
    def copy_keycloak_listener():
        cedar_home = Path(Util.cedar_home)
        keycloak_home_value = os.environ.get('CEDAR_KEYCLOAK_HOME')
        if not keycloak_home_value:
            raise DevError("CEDAR_KEYCLOAK_HOME is not set. Load the target CEDAR profile first.")
        source = cedar_home / 'cedar-keycloak-event-listener' / 'target' / 'cedar-keycloak-event-listener.jar'
        keycloak_home = Path(keycloak_home_value)
        provider_dir = keycloak_home / 'providers'
        build_script = keycloak_home / 'bin' / 'kc.sh'
        missing = []
        if not source.is_file():
            missing.append(str(source))
        if not provider_dir.is_dir():
            missing.append(str(provider_dir))
        if not build_script.is_file():
            missing.append(str(build_script))
        if missing:
            raise DevError(f"Cannot install the Keycloak listener; missing: {', '.join(missing)}")
        command = (
            f"cp {shlex.quote(str(source))} {shlex.quote(str(provider_dir))}/"
            " && "
            f"{shlex.quote(str(build_script))} build"
        )
        return Worker.execute_generic_shell_commands(
            [command],
            cwd=str(keycloak_home / 'bin'),
            title="Installing the CEDAR Keycloak event listener",
        )

    @staticmethod
    def generate_api_key(user_id: str):
        if Const.CEDAR_SALT_API_KEY in os.environ:
            salt = os.environ[Const.CEDAR_SALT_API_KEY]
        else:
            salt = 'saltme'

        digest = hashlib.sha256()

        digest.update(salt.encode('utf-8'))
        digest.update(user_id.encode('utf-8'))
        hash_bytes = digest.digest()

        for _ in range(1000):
            digest = hashlib.sha256()
            digest.update(hash_bytes)
            hash_bytes = digest.digest()

        api_key = binascii.hexlify(hash_bytes).decode('utf-8')

        table = Table("Name", "Value", title="Generated CEDAR apiKey")
        table.add_row('userId', user_id)
        table.add_row('apiKey', api_key)
        table.style = Style(color="green")
        console.print(table)
        return api_key

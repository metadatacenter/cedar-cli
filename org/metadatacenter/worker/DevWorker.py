import binascii
import hashlib
import os

from rich.console import Console
from rich.style import Style
from rich.table import Table

from org.metadatacenter.util.Const import Const
from org.metadatacenter.worker.Worker import Worker

console = Console()


class DevWorker(Worker):

    def __init__(self):
        super().__init__()

    # TODO think about recreating this functionality in Python
    @staticmethod
    def create_directories():
        Worker.execute_generic_shell_commands([
            """
echo "Creating CEDAR directories for local development"

mkdir -p ${CEDAR_HOME}/cache/terminology/
mkdir -p ${CEDAR_HOME}/CEDAR_CA/
mkdir -p ${CEDAR_HOME}/export/
mkdir -p ${CEDAR_HOME}/tmp/

mkdir -p ${CEDAR_HOME}/log/frontend-artifacts/
mkdir -p ${CEDAR_HOME}/log/frontend-bridging/
mkdir -p ${CEDAR_HOME}/log/frontend-cedar/
mkdir -p ${CEDAR_HOME}/log/frontend-component/
mkdir -p ${CEDAR_HOME}/log/frontend-cee-demo-angular/
mkdir -p ${CEDAR_HOME}/log/frontend-cee-demo-angular-dist/
mkdir -p ${CEDAR_HOME}/log/frontend-cee-docs-angular/
mkdir -p ${CEDAR_HOME}/log/frontend-cee-docs-angular-dist/
mkdir -p ${CEDAR_HOME}/log/frontend-monitoring/
mkdir -p ${CEDAR_HOME}/log/frontend-openview/
mkdir -p ${CEDAR_HOME}/log/frontend-shared/

mkdir -p ${CEDAR_HOME}/log/server-artifact/
mkdir -p ${CEDAR_HOME}/log/server-auth
mkdir -p ${CEDAR_HOME}/log/server-bridge/
mkdir -p ${CEDAR_HOME}/log/server-group
mkdir -p ${CEDAR_HOME}/log/server-impex/
mkdir -p ${CEDAR_HOME}/log/server-messaging
mkdir -p ${CEDAR_HOME}/log/server-monitor
mkdir -p ${CEDAR_HOME}/log/server-open
mkdir -p ${CEDAR_HOME}/log/server-repo
mkdir -p ${CEDAR_HOME}/log/server-resource
mkdir -p ${CEDAR_HOME}/log/server-schema
mkdir -p ${CEDAR_HOME}/log/server-submission
mkdir -p ${CEDAR_HOME}/log/server-terminology
mkdir -p ${CEDAR_HOME}/log/server-user
mkdir -p ${CEDAR_HOME}/log/server-valuerecommender
mkdir -p ${CEDAR_HOME}/log/server-worker

mkdir -p ${CEDAR_HOME}/log/cadsr-tools/

mkdir -p ${CEDAR_HOME}/log/nginx/
"""
        ],
            title="Creating all CEDAR log and working directories",
        )

    # TODO think about recreating this functionality in Python, or at least generate the list of hosts
    # from the known configuration
    @staticmethod
    def add_hosts():
        Worker.execute_generic_shell_commands([
            """
CEDAR_HOSTS=(
    "artifact"
    "artifacts"
    "bridge"
    "bridging"
    "auth"
    "cedar"
    "component"
    "group"
    "impex"
    "monitor"
    "monitoring"
    "messaging"
    "open"
    "openview"
    "repo"
    "resource"
    "schema"
    "submission"
    "terminology"
    "user"
    "valuerecommender"
    "worker"
    "demo.cee"
    "demo-dist.cee"
    "docs.cee"
    "docs-dist.cee"
)

counter=0
echo "Testing the list of CEDAR hosts:"
for i in "${CEDAR_HOSTS[@]}"
do
  HOST=$i.${CEDAR_HOST}
  ping -c 1 ${HOST} > /dev/null 2>&1

  if [[ $? != 0 ]];
  then
    echo "Host unknown :" ${HOST}
    hosts[$counter]=${HOST}
    ((counter++))
  else
    echo "Host known   :" ${HOST}
  fi
done

echo

if [[ ${#hosts[@]} == 0 ]];
then
  echo "All CEDAR hosts are known, nothing to do"
else
  echo "Some CEDAR hosts are unknown, we will prompt for your password in order to make modifications to /etc/hosts !"
  echo
  STR="$'\n'$'\n'# Added by CEDAR install process on $(date +%Y-%m-%d) [YYYY-mm-dd] from here:$'\n'"
  sudo bash -c "echo ${STR} >> /etc/hosts"
  for i in "${hosts[@]}"
  do
    echo "Host unknown, adding to /etc/hosts:" $i
    STR="127.0.0.1$'\t'$i"
    sudo bash -c "echo ${STR} >> /etc/hosts"
  done
  STR="$'\n'# Added by CEDAR install process until here.$'\n'"
  sudo bash -c "echo ${STR} >> /etc/hosts"
fi

echo
"""
        ],
            title="Adding CEDAR hostnames to /etc/hosts",
        )

    @staticmethod
    def copy_keycloak_listener():
        Worker.execute_generic_shell_commands([
            """
cp $CEDAR_HOME/cedar-keycloak-event-listener/target/cedar-keycloak-event-listener.jar ${CEDAR_KEYCLOAK_HOME}/providers/.
cd ${CEDAR_KEYCLOAK_HOME}/bin
./kc.sh build
"""
        ],
            title="Adding CEDAR hostnames to /etc/hosts",
        )

    @staticmethod
    def generate_api_key(user_id: str):
        if Const.CEDAR_SALT_API_KEY in os.environ:
            salt = os.environ[Const.CEDAR_SALT_API_KEY]
        else:
            salt = 'saltme'

        try:
            digest = hashlib.sha256()
        except Exception as e:
            print(f"Error while building SHA-256 digest: {e}")
            return None

        digest.update(salt.encode('utf-8'))
        digest.update(user_id.encode('utf-8'))
        hash_bytes = digest.digest()

        for _ in range(1000):
            digest = hashlib.sha256()
            digest.update(hash_bytes)
            hash_bytes = digest.digest()

        api_key = binascii.hexlify(hash_bytes).decode('utf-8')

        table = Table("Name", "Value", title="Generated CEDAR apiKey")
        table.add_row('salt', salt)
        table.add_row('userId', user_id)
        table.add_row('apiKey', api_key)
        table.style = Style(color="green")
        console.print(table)

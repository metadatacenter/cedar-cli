from rich.console import Console

from org.metadatacenter.worker.Worker import Worker

console = Console()


class DevWorker(Worker):

    def __init__(self):
        super().__init__()

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

mkdir -p ${CEDAR_HOME}/log/proxy-cee-demo-api-php/

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

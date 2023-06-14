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

mkdir -p ${CEDAR_HOME}/log/cedar-frontend-artifacts/
mkdir -p ${CEDAR_HOME}/log/cedar-frontend-bridging/
mkdir -p ${CEDAR_HOME}/log/cedar-frontend-cedar/
mkdir -p ${CEDAR_HOME}/log/cedar-frontend-component/
mkdir -p ${CEDAR_HOME}/log/cedar-frontend-main/
mkdir -p ${CEDAR_HOME}/log/cedar-frontend-monitoring/
mkdir -p ${CEDAR_HOME}/log/cedar-frontend-openview/

mkdir -p ${CEDAR_HOME}/log/cedar-shared/

mkdir -p ${CEDAR_HOME}/log/cedar-auth-server/

mkdir -p ${CEDAR_HOME}/log/cedar-cadsr-tools/

mkdir -p ${CEDAR_HOME}/log/cedar-artifact-server/
mkdir -p ${CEDAR_HOME}/log/cedar-bridge-server/
mkdir -p ${CEDAR_HOME}/log/cedar-group-server/
mkdir -p ${CEDAR_HOME}/log/cedar-monitor-server/
mkdir -p ${CEDAR_HOME}/log/cedar-messaging-server/
mkdir -p ${CEDAR_HOME}/log/cedar-openview-server/
mkdir -p ${CEDAR_HOME}/log/cedar-repo-server/
mkdir -p ${CEDAR_HOME}/log/cedar-resource-server/
mkdir -p ${CEDAR_HOME}/log/cedar-schema-server/
mkdir -p ${CEDAR_HOME}/log/cedar-submission-server/
mkdir -p ${CEDAR_HOME}/log/cedar-terminology-server/
mkdir -p ${CEDAR_HOME}/log/cedar-user-server/
mkdir -p ${CEDAR_HOME}/log/cedar-valuerecommender-server/
mkdir -p ${CEDAR_HOME}/log/cedar-worker-server/
mkdir -p ${CEDAR_HOME}/log/cedar-impex-server
mkdir -p ${CEDAR_HOME}/log/cedar-template-editor
mkdir -p ${CEDAR_HOME}/log/cedar-component-distribution
mkdir -p ${CEDAR_HOME}/log/cedar-cee-demo-angular-dist
mkdir -p ${CEDAR_HOME}/log/cedar-cee-demo-angular
mkdir -p ${CEDAR_HOME}/log/cedar-cee-docs-angular-dist
mkdir -p ${CEDAR_HOME}/log/cedar-cee-docs-angular
mkdir -p ${CEDAR_HOME}/log/cedar-openview

mkdir -p ${CEDAR_HOME}/log/nginx/
"""
        ],
            title="Creating all CEDAR log and working directories",
        )

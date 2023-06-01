from rich.console import Console

from org.metadatacenter.worker.Worker import Worker

console = Console()

GIT_STATUS_CHAR_LIMIT = 300


class DockerWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def create_network():
        Worker.execute_generic_shell_commands([
            """
echo 'Checking previous Docker network ...'
if docker network ls | grep 'cedarnet' > /dev/null 2>&1
then
    echo 'Removing previous Docker network ...'
    docker network remove cedarnet
else
    echo 'Previous network not present, nothing to do.'
    echo
fi
echo 'Creating Docker network: cedarnet ...'
docker network create --subnet=${CEDAR_NET_SUBNET}/24 --gateway ${CEDAR_NET_GATEWAY} cedarnet
"""
        ],
            title="Creating CEDAR Docker network",
        )

    @staticmethod
    def create_certificates_volume():
        Worker.execute_generic_shell_commands([
            """
echo 'Creating volume for SSL certificates...'
docker volume create cedar_cert
"""
        ],
            title="Creating CEDAR volume for certificates",
        )

    @staticmethod
    def copy_certificates():
        Worker.execute_generic_shell_commands([
            """
echo "Copying self-signed certificates into the cedar_cert volume..."
docker run -v cedar_cert:/data --name cedar-cert-helper busybox:1.36.0 true
docker cp ${CEDAR_HOME}/cedar-docker-deploy/cedar-assets/cert/live cedar-cert-helper:/data
docker rm cedar-cert-helper

echo "Copying CA certificate into the cedar_ca volume..."
docker run -v cedar_ca:/data --name cedar-ca-helper busybox:1.36.0 true
docker cp ${CEDAR_HOME}/cedar-docker-deploy/cedar-assets/ca/ca.crt cedar-ca-helper:/data
docker rm cedar-ca-helper
"""
        ],
            title="Copy CEDAR self-signed certificates",
        )

    @staticmethod
    def remove_containers():
        Worker.execute_generic_shell_commands([
            """
docker ps -a | grep "metadatacenter/cedar-.*" | awk '{print $1}' | xargs docker rm
"""
        ],
            title="Removing all CEDAR containers",
        )

    @staticmethod
    def remove_images():
        Worker.execute_generic_shell_commands([
            """
docker images | grep "metadatacenter/cedar-.*" | awk '{print $3}' | xargs docker rmi
"""
        ],
            title="Removing all CEDAR images",
        )

    @staticmethod
    def remove_network():
        Worker.execute_generic_shell_commands([
            """
docker network rm cedarnet
"""
        ],
            title="Removing CEDAR network",
        )

    @staticmethod
    def remove_volumes():
        Worker.execute_generic_shell_commands([
            """
docker volume rm cedar_ca
docker volume rm cedar_cert
docker volume rm opensearch_data
docker volume rm opensearch_log
docker volume rm keycloak_log
docker volume rm mongo_data
docker volume rm mongo_log
docker volume rm mongo_state
docker volume rm mysql_data
docker volume rm mysql_log
docker volume rm neo4j_data
docker volume rm neo4j_log
docker volume rm neo4j_state
docker volume rm nginx_log
docker volume rm redis_data
docker volume rm redis_log

docker volume rm terminology_data

docker volume rm group_log
docker volume rm impex_log
docker volume rm monitor_log
docker volume rm messaging_log
docker volume rm openview_log
docker volume rm repo_log
docker volume rm resource_log
docker volume rm schema_log
docker volume rm submission_log
docker volume rm artifact_log
docker volume rm terminology_log
docker volume rm user_log
docker volume rm valuerecommender_log
docker volume rm worker_log
docker volume rm resource_state
docker volume rm bridge_log

docker volume rm frontend_editor_log
docker volume rm frontend_openview_log
docker volume rm frontend_component_log
"""
        ],
            title="Removing all CEDAR volumes",
        )

import json
import os
import subprocess

from rich.console import Console
from rich.table import Table

from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()

GIT_STATUS_CHAR_LIMIT = 300


class DockerWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def validate():
        Worker.execute_generic_shell_commands([
            """
failed=0
for stack in cedar-infrastructure cedar-microservices cedar-frontend cedar-admin; do
    out=$(cd "${CEDAR_HOME}/cedar-docker-deploy/${stack}" && docker compose config --quiet 2>&1)
    rc=$?
    undefined=$(echo "${out}" | grep 'variable is not set' | grep -oE 'CEDAR_[A-Z0-9_]+' | sort -u)
    if [ ${rc} -ne 0 ]; then
        echo "FAIL ${stack}: compose file is not valid"
        echo "${out}"
        failed=1
    elif [ -n "${undefined}" ]; then
        echo "FAIL ${stack}: referenced but not defined by the profile:"
        echo "${undefined}" | sed 's/^/         /'
        failed=1
    else
        echo "OK   ${stack}"
    fi
done
if [ ${failed} -ne 0 ]; then
    echo
    echo "Validation failed. Source a Docker profile before running this,"
    echo "for example cedar-development/bin/templates/cedar-profile-docker-eval.sh."
fi
exit ${failed}
"""
        ],
            title="Validating CEDAR compose stacks",
        )

    @staticmethod
    def _docker_command(arguments, cwd=None):
        try:
            return subprocess.run(
                ['docker', *arguments],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            return subprocess.CompletedProcess(
                ['docker', *arguments],
                127,
                stdout='',
                stderr=str(error),
            )

    @staticmethod
    def _docker_server_version():
        result = DockerWorker._docker_command(['info', '--format', '{{.ServerVersion}}'])
        if result.returncode != 0:
            return None, result.stderr.strip() or 'Docker daemon is unavailable'
        return result.stdout.strip(), None

    @staticmethod
    def _expected_compose_services(stack_directory):
        result = DockerWorker._docker_command(
            ['compose', 'config', '--no-interpolate', '--services'],
            cwd=stack_directory,
        )
        if result.returncode != 0:
            return [], result.stderr.strip() or 'Unable to read the Compose project'
        return [line for line in result.stdout.splitlines() if line], None

    @staticmethod
    def _compose_containers(project_name):
        result = DockerWorker._docker_command([
            'ps', '-aq',
            '--filter', f'label=com.docker.compose.project={project_name}',
        ])
        if result.returncode != 0:
            return {}, result.stderr.strip() or 'Unable to list Docker containers'

        container_ids = result.stdout.split()
        if not container_ids:
            return {}, None

        result = DockerWorker._docker_command(['inspect', *container_ids])
        if result.returncode != 0:
            return {}, result.stderr.strip() or 'Unable to inspect Docker containers'

        try:
            inspected = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            return {}, f'Unable to parse docker inspect output: {error}'

        containers = {}
        for container in inspected:
            labels = container.get('Config', {}).get('Labels') or {}
            service = labels.get('com.docker.compose.service')
            if service is None:
                continue
            # A replacement can briefly coexist with its predecessor. The ISO timestamp sorts
            # chronologically, so report the newest container for that Compose service.
            previous = containers.get(service)
            if previous is None or container.get('Created', '') > previous.get('Created', ''):
                containers[service] = container
        return containers, None

    @staticmethod
    def _container_report(container):
        if container is None:
            return '❌', '', 'missing'

        state = container.get('State') or {}
        runtime_state = state.get('Status', 'unknown')
        health = (state.get('Health') or {}).get('Status')
        name = container.get('Name', '').lstrip('/')

        if runtime_state == 'running' and health in (None, 'healthy'):
            return '✅', name, health or 'running (no healthcheck)'
        if runtime_state == 'running' and health == 'starting':
            return '⏳', name, 'healthcheck starting'
        if runtime_state == 'running':
            return '❌', name, health or runtime_state

        detail = runtime_state
        state_error = state.get('Error')
        if state_error:
            detail += f': {state_error}'
        return '❌', name, detail

    @staticmethod
    def status(include_frontends=True, include_admin=False):
        """Report the Compose inventory and container health for a Docker deployment.

        The native ``cedarcli status`` intentionally continues to probe host ports. Docker keeps
        several service and admin ports private to cedarnet, so it needs a separate source of truth:
        the expected Compose services plus Docker's runtime and health state.
        """
        server_version, daemon_error = DockerWorker._docker_server_version()
        if daemon_error:
            console.print(f'[red]❌ Docker status unavailable:[/red] {daemon_error}')
            return False

        stack_names = ['infrastructure', 'microservices']
        if include_frontends:
            stack_names.append('frontends')
        if include_admin:
            stack_names.append('admin')

        table = Table(
            'Stack', 'Service', 'Status', 'Container', 'Detail',
            title=f'CEDAR Docker status (Engine {server_version})',
        )
        expected_count = 0
        healthy_count = 0

        for stack_name in stack_names:
            directory, _ = DockerWorker.STACKS[stack_name]
            stack_directory = os.path.join(Util.cedar_home, 'cedar-docker-deploy', directory)
            services, compose_error = DockerWorker._expected_compose_services(stack_directory)

            if compose_error:
                expected_count += 1
                table.add_row(stack_name, 'Compose project', '❌', '', compose_error)
                continue
            if not services:
                expected_count += 1
                table.add_row(stack_name, 'Compose project', '❌', '', 'no services defined')
                continue

            containers, container_error = DockerWorker._compose_containers(directory)
            if container_error:
                expected_count += len(services)
                for service in services:
                    table.add_row(stack_name, service, '❌', '', container_error)
                continue

            for service in services:
                expected_count += 1
                indicator, container_name, detail = DockerWorker._container_report(containers.get(service))
                if indicator == '✅':
                    healthy_count += 1
                table.add_row(stack_name, service, indicator, container_name, detail)

        console.print(table)
        if healthy_count == expected_count:
            console.print(f'[green]✅ {healthy_count}/{expected_count} required Docker services are ready.[/green]')
            return True

        console.print(
            f'[red]❌ {healthy_count}/{expected_count} required Docker services are ready.[/red] '
            'Use docker compose logs for the failing service.'
        )
        return False

    @staticmethod
    def build_images(images, local=False):
        """Build the given images in order. Returns a process exit code.

        With local=True the jar is staged from the checkout before each image that carries one, and
        cleared afterwards: a staged jar is an input to one build, not a mode the tree stays in.
        Staging is strict, so a target whose jar has not been built fails rather than quietly
        falling back to the published one.
        """
        from org.metadatacenter.util.DockerImages import DockerImages

        _, version, prefix = DockerImages.manifest()
        build_home = DockerImages.build_home()

        # The locked server versions travel from the manifest into every build as build arguments.
        # Passing them to all images rather than working out which image wants which is deliberate:
        # Docker ignores a build argument a Dockerfile does not declare, and the alternative is a
        # second place recording which image installs which server.
        build_args = ' '.join(
            f'--build-arg {name}="{value}"' for name, value in sorted(DockerImages.server_versions().items())
        )

        steps = []
        for image in images:
            stage = local and DockerImages.stageable(image)
            steps.append(f"""
echo "==> {image}"
{f'"{build_home}/bin/stage-local-jar.sh" {image} || exit 1' if stage else ''}
docker build {build_args} -t "{prefix}/{image}:{version}" "{build_home}/{image}"
rc=$?
{f'rm -f "{build_home}/{image}/local/"*.jar' if stage else ''}
if [ $rc -ne 0 ]; then
    echo "Build failed: {image}"
    exit $rc
fi
""")

        out = Worker.execute_generic_shell_commands(
            ["set -o pipefail\n" + "\n".join(steps) + "\necho 'All requested images built.'"],
            title=f"Building {len(images)} CEDAR image(s) at {version}",
        )
        return 0 if any("All requested images built." in line for line in out) else 1

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
export CEDAR_CUSTOM_CERT=false
if [[ -e ${CEDAR_HOME}/CEDAR_CA/certs/-${CEDAR_HOST}/${CEDAR_HOST}.crt ]]; then export CEDAR_CUSTOM_CERT=true; fi
if [[ $CEDAR_CUSTOM_CERT == 'true' ]]; then docker cp ${CEDAR_HOME}/CEDAR_CA/certs cedar-cert-helper:/data; fi
if [[ $CEDAR_CUSTOM_CERT != 'true' ]]; then docker cp ${CEDAR_HOME}/cedar-docker-deploy/cedar-assets/cert/certs cedar-cert-helper:/data; fi
docker rm cedar-cert-helper

echo "Copying CA certificate into the cedar_ca volume..."
docker run -v cedar_ca:/data --name cedar-ca-helper busybox:1.36.0 true
if [[ $CEDAR_CUSTOM_CERT == 'true' ]]; then docker cp ${CEDAR_HOME}/CEDAR_CA/ca.crt cedar-ca-helper:/data; fi
if [[ $CEDAR_CUSTOM_CERT != 'true' ]]; then docker cp ${CEDAR_HOME}/cedar-docker-deploy/cedar-assets/ca/ca.crt cedar-ca-helper:/data; fi
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
docker volume rm log_opensearch

docker volume rm keycloak_state
docker volume rm log_keycloak

docker volume rm mongo_data
docker volume rm mongo_state
docker volume rm mongo_configdb
docker volume rm log_mongo

docker volume rm mysql_data
docker volume rm log_mysql

docker volume rm neo4j_data
docker volume rm neo4j_state
docker volume rm log_neo4j

docker volume rm log_nginx

docker volume rm redis_data
docker volume rm log_redis


docker volume rm terminology_data


docker volume rm log_group
docker volume rm log_impex
docker volume rm log_monitor
docker volume rm log_messaging
docker volume rm log_openview
docker volume rm log_repo
docker volume rm log_resource
docker volume rm log_schema
docker volume rm log_submission
docker volume rm log_artifact
docker volume rm log_terminology
docker volume rm log_user
docker volume rm log_valuerecommender
docker volume rm log_worker
docker volume rm log_bridge

docker volume rm resource_state

docker volume rm log_frontend_main
docker volume rm log_frontend_openview
docker volume rm log_frontend_content
docker volume rm log_frontend_monitoring
docker volume rm log_frontend_bridging
"""
        ],
            title="Removing all CEDAR volumes",
        )

    # Stack name in cedar-docker-deploy, and what to call it when talking to the user.
    STACKS = {
        'infrastructure': ('cedar-infrastructure', 'infrastructure services'),
        'microservices': ('cedar-microservices', 'microservices'),
        'frontends': ('cedar-frontend', 'frontends'),
        'admin': ('cedar-admin', 'admin tools'),
    }

    @staticmethod
    def compose(stack, action, detach=False):
        directory, label = DockerWorker.STACKS[stack]
        command = 'docker compose ' + action
        if action == 'up' and detach:
            command += ' -d'
        Worker.execute_generic_shell_commands(
            [command],
            title=("Starting" if action == 'up' else "Stopping") + " CEDAR " + label,
            cwd=os.path.join(Util.cedar_home, 'cedar-docker-deploy', directory)
        )

    @staticmethod
    def start_infrastructure(detach=False):
        DockerWorker.compose('infrastructure', 'up', detach)

    @staticmethod
    def start_microservices(detach=False):
        DockerWorker.compose('microservices', 'up', detach)

    @staticmethod
    def start_frontends(detach=False):
        DockerWorker.compose('frontends', 'up', detach)

    @staticmethod
    def start_admin(detach=False):
        DockerWorker.compose('admin', 'up', detach)

    @staticmethod
    def stop_infrastructure():
        DockerWorker.compose('infrastructure', 'down')

    @staticmethod
    def stop_microservices():
        DockerWorker.compose('microservices', 'down')

    @staticmethod
    def stop_frontends():
        DockerWorker.compose('frontends', 'down')

    @staticmethod
    def stop_admin():
        DockerWorker.compose('admin', 'down')

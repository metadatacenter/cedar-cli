import json
import os
import re
import shlex
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request

from rich.console import Console
from rich.table import Table

from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()

GIT_STATUS_CHAR_LIMIT = 300

FRONTEND_NAMES = (
    'EDITOR',
    'CONTENT',
    'OPENVIEW',
    'MONITORING',
    'BRIDGING',
    'WORKSPACE',
    'DESIGNER',
)

FRONTEND_PUBLIC_HOSTS = (
    'cedar',
    'workspace',
    'designer',
    'openview',
    'content',
    'monitoring',
    'bridging',
)

FRONTEND_COMPOSE_SERVICES = {
    'main': 'frontend-main',
    'openview': 'frontend-openview',
    'monitoring': 'frontend-monitoring',
    'bridging': 'frontend-bridging',
    'content': 'frontend-content',
    'workspace': 'frontend-workspace',
    'designer': 'frontend-template-designer',
}

MICROSERVICE_COMPOSE_SERVICES = {
    'artifact': 'server-artifact',
    'bridge': 'server-bridge',
    'group': 'server-group',
    'impex': 'server-impex',
    'messaging': 'server-messaging',
    'monitor': 'server-monitor',
    'open': 'server-openview',
    'repo': 'server-repo',
    'resource': 'server-resource',
    'schema': 'server-schema',
    'submission': 'server-submission',
    'terminology': 'server-terminology',
    'user': 'server-user',
    'valuerecommender': 'server-valuerecommender',
    'worker': 'server-worker',
}

STATUS_SERVICE_ORDER = {
    'infrastructure': (
        'mysql',
        'mongo',
        'redis-persistent',
        'opensearch',
        'neo4j',
        'keycloak',
        'nginx',
    ),
    'microservices': tuple(MICROSERVICE_COMPOSE_SERVICES.values()),
    'frontends': tuple(FRONTEND_COMPOSE_SERVICES.values()),
    'admin': (
        'redis-commander',
        'kibana',
        'phpmyadmin',
        'admin-tool',
    ),
}


class DockerWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def validate(environment=None):
        from org.metadatacenter.util.DockerImages import DockerImages

        try:
            prefix = DockerImages.image_prefix(environment)
            base_prefix = DockerImages.base_image_prefix(environment)
        except ValueError as error:
            console.print(f'[red]FAIL Docker image configuration: {error}[/red]')
            return 1

        validation_environment = (os.environ if environment is None else environment).copy()
        validation_environment['CEDAR_IMAGE_PREFIX'] = prefix
        validation_environment['CEDAR_BASE_IMAGE_PREFIX'] = base_prefix

        output = Worker.execute_generic_shell_commands([
            """
failed=0
for stack in cedar-infrastructure cedar-microservices cedar-frontend cedar-admin; do
    out=$(cd "${CEDAR_HOME}/cedar-docker-deploy/${stack}" && docker compose config --quiet 2>&1)
    rc=$?
    undefined=$(echo "${out}" | grep 'variable is not set' | grep -oE 'CEDAR_[A-Z0-9_]+' | sort -u)
    images=$(cd "${CEDAR_HOME}/cedar-docker-deploy/${stack}" && docker compose config --images 2>&1)
    images_rc=$?
    inconsistent=$(echo "${images}" | awk -v expected="${CEDAR_IMAGE_PREFIX}/cedar-" \
        'index($0, "/cedar-") && index($0, expected) != 1')
    if [ ${rc} -ne 0 ]; then
        echo "FAIL ${stack}: compose file is not valid"
        echo "${out}"
        failed=1
    elif [ -n "${undefined}" ]; then
        echo "FAIL ${stack}: referenced but not defined by the profile:"
        echo "${undefined}" | sed 's/^/         /'
        failed=1
    elif [ ${images_rc} -ne 0 ]; then
        echo "FAIL ${stack}: compose image references could not be resolved"
        echo "${images}"
        failed=1
    elif [ -n "${inconsistent}" ]; then
        echo "FAIL ${stack}: CEDAR images do not use CEDAR_IMAGE_PREFIX=${CEDAR_IMAGE_PREFIX}:"
        echo "${inconsistent}" | sed 's/^/         /'
        failed=1
    else
        echo "OK   ${stack}"
    fi
done
if [ ${failed} -ne 0 ]; then
    echo
    echo "Validation failed. Check the configured CEDAR mode and its Docker inputs."
fi
exit ${failed}
"""
        ],
            title="Validating CEDAR compose stacks",
            env=validation_environment,
            show_command=False,
        )
        return output.returncode

    @staticmethod
    def _docker_command(arguments, cwd=None, environment=None):
        try:
            return subprocess.run(
                ['docker', *arguments],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                env=environment,
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
    def _expected_compose_services(stack_directory, environment=None):
        result = DockerWorker._docker_command(
            ['compose', 'config', '--no-interpolate', '--services'],
            cwd=stack_directory,
            environment=environment,
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
    def _stack_names(mode):
        names = ['infrastructure', 'microservices']
        if mode.includes_frontend_containers:
            names.append('frontends')
        return names

    @staticmethod
    def _mode_label(mode):
        return 'docker' if mode is DockerDeploymentMode.FULL else mode.value

    @staticmethod
    def _deployment_state_path():
        return os.path.join(Util.cedar_home, '.cedar', 'docker-deployment.json')

    @staticmethod
    def active_deployment():
        """Return the last aggregate deployment mode, if recorded."""
        try:
            with open(DockerWorker._deployment_state_path(), 'r', encoding='utf-8') as state_file:
                state = json.load(state_file)
            return DockerDeploymentMode(state['mode'])
        except (FileNotFoundError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def running_compose_projects():
        """Return running CEDAR Compose projects; daemon absence means none are running."""
        result = DockerWorker._docker_command([
            'ps', '--format', '{{.Label "com.docker.compose.project"}}',
        ])
        if result.returncode != 0:
            return set()
        known = {directory for directory, _label in DockerWorker.STACKS.values()}
        return {
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() in known
        }

    @staticmethod
    def _record_active_deployment(mode, train=None):
        state_path = DockerWorker._deployment_state_path()
        state_directory = os.path.dirname(state_path)
        os.makedirs(state_directory, exist_ok=True)
        temporary_path = state_path + '.tmp'
        with open(temporary_path, 'w', encoding='utf-8') as state_file:
            state = {'mode': mode.value}
            if train:
                state['train'] = train
            json.dump(state, state_file, indent=2)
            state_file.write('\n')
        os.replace(temporary_path, state_path)

    @staticmethod
    def _clear_active_deployment():
        try:
            os.remove(DockerWorker._deployment_state_path())
        except FileNotFoundError:
            pass

    @staticmethod
    def active_train():
        try:
            with open(DockerWorker._deployment_state_path(), 'r', encoding='utf-8') as state_file:
                return json.load(state_file).get('train')
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def mode_environment(mode):
        """Build a child environment for one Docker deployment mode without changing the shell."""
        environment = os.environ.copy()
        errors = []
        nginx_host = environment.get('CEDAR_NGINX_HOST')
        cedar_host = environment.get('CEDAR_HOST')
        if not nginx_host:
            errors.append('CEDAR_NGINX_HOST is not defined')
        if not cedar_host:
            errors.append('CEDAR_HOST is not defined')

        missing_container_hosts = []
        for frontend in FRONTEND_NAMES:
            container_variable = f'CEDAR_FRONTEND_{frontend}_CONTAINER_HOST'
            upstream_variable = f'CEDAR_FRONTEND_{frontend}_HOST'
            container_host = environment.get(container_variable)
            if not container_host:
                missing_container_hosts.append(container_variable)
                continue
            environment[upstream_variable] = (
                'host.docker.internal'
                if mode is DockerDeploymentMode.HYBRID
                else container_host
            )

        if len(missing_container_hosts) == len(FRONTEND_NAMES):
            errors.append(
                'CEDAR Docker environment is incomplete; run '
                'cedarcli mode docker or cedarcli mode hybrid'
            )
        else:
            errors.extend(f'{variable} is not defined' for variable in missing_container_hosts)

        if nginx_host:
            environment['CEDAR_AUTH_HOST_TARGET'] = nginx_host
        environment['CEDAR_DOCKER_MODE'] = mode.value
        return environment, errors

    @staticmethod
    def _container_snapshot(stack_names, environment=None):
        server_version, daemon_error = DockerWorker._docker_server_version()
        snapshot = {
            'server_version': server_version,
            'daemon_error': daemon_error,
            'rows': [],
            'expected': 0,
            'healthy': 0,
        }
        if daemon_error:
            return snapshot

        for stack_name in stack_names:
            directory, _ = DockerWorker.STACKS[stack_name]
            stack_directory = os.path.join(Util.cedar_home, 'cedar-docker-deploy', directory)
            services, compose_error = DockerWorker._expected_compose_services(
                stack_directory,
                environment=environment,
            )
            services = DockerWorker._ordered_status_services(stack_name, services)

            if compose_error:
                snapshot['expected'] += 1
                snapshot['rows'].append((stack_name, 'Compose project', '❌', '', compose_error))
                continue
            if not services:
                snapshot['expected'] += 1
                snapshot['rows'].append((stack_name, 'Compose project', '❌', '', 'no services defined'))
                continue

            containers, container_error = DockerWorker._compose_containers(directory)
            if container_error:
                snapshot['expected'] += len(services)
                for service in services:
                    snapshot['rows'].append((stack_name, service, '❌', '', container_error))
                continue

            for service in services:
                snapshot['expected'] += 1
                indicator, container_name, detail = DockerWorker._container_report(containers.get(service))
                if indicator == '✅':
                    snapshot['healthy'] += 1
                snapshot['rows'].append((stack_name, service, indicator, container_name, detail))

        return snapshot

    @staticmethod
    def _ordered_status_services(stack_name, services):
        """Use a stable human-facing order and retain unknown future services at the end."""
        preferred = STATUS_SERVICE_ORDER.get(stack_name, ())
        rank = {service: position for position, service in enumerate(preferred)}
        return sorted(
            services,
            key=lambda service: (rank.get(service, len(preferred)), service),
        )

    @staticmethod
    def _snapshot_ready(snapshot):
        return (
            snapshot['daemon_error'] is None
            and snapshot['expected'] > 0
            and snapshot['healthy'] == snapshot['expected']
        )

    @staticmethod
    def _render_snapshot(snapshot, mode):
        if snapshot['daemon_error']:
            console.print(
                "[red]❌ Docker is unavailable.[/red] Start Docker Desktop or repair "
                "Docker daemon access, then retry."
            )
            return

        table = Table(
            'Stack', 'Service', 'Status', 'Container', 'Detail',
            title=(f"CEDAR Docker status: {DockerWorker._mode_label(mode)} "
                   f"(Engine {snapshot['server_version']})"),
        )
        for row in snapshot['rows']:
            table.add_row(*row)
        console.print(table)

    @staticmethod
    def _backend_auth_error(timeout=10):
        cedar_host = os.environ.get('CEDAR_HOST')
        if not cedar_host:
            return 'CEDAR_HOST is not defined; cannot check backend authentication routing'
        url = f'https://auth.{cedar_host}/realms/CEDAR/.well-known/openid-configuration'
        result = DockerWorker._docker_command([
            'exec', 'server-resource', 'curl', '-kfsS', '--max-time', str(max(1, int(timeout))), url,
        ])
        if result.returncode == 0:
            return None
        return result.stderr.strip() or result.stdout.strip() or f'could not fetch {url} from server-resource'

    @staticmethod
    def _url_error(url, timeout=10):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
        )
        try:
            with opener.open(url, timeout=timeout) as response:
                if response.status == 200:
                    return None
                return f'{url} returned HTTP {response.status}'
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return f'{url} is not ready: {error}'

    @staticmethod
    def _frontend_route_errors(timeout=10):
        cedar_host = os.environ.get('CEDAR_HOST')
        if not cedar_host:
            return ['CEDAR_HOST is not defined; cannot check frontend routes']
        errors = []
        for host in FRONTEND_PUBLIC_HOSTS:
            url = f'https://{host}.{cedar_host}/'
            error = DockerWorker._url_error(url, timeout=timeout)
            if error:
                errors.append(error)
        return errors

    @staticmethod
    def _acceptance_errors(mode, timeout=10):
        errors = []
        auth_error = DockerWorker._backend_auth_error(timeout=timeout)
        if auth_error:
            errors.append(f'backend authentication route: {auth_error}')
        if mode.checks_frontend_routes:
            errors.extend(DockerWorker._frontend_route_errors(timeout=timeout))
        return errors

    @staticmethod
    def status(mode=DockerDeploymentMode.FULL):
        """Report container health and the acceptance checks selected by the deployment mode."""
        if isinstance(mode, str):
            mode = DockerDeploymentMode(mode)

        environment, environment_errors = DockerWorker.mode_environment(mode)
        active_train = DockerWorker.active_train()
        if active_train:
            environment['CEDAR_DOCKER_VERSION'] = active_train
        if environment_errors:
            for error in environment_errors:
                console.print(f'[red]❌ {error}[/red]')
            return False

        console.print(
            f'Docker image set: {active_train}' if active_train
            else 'Docker image set: local development tag'
        )

        snapshot = DockerWorker._container_snapshot(
            DockerWorker._stack_names(mode),
            environment=environment,
        )
        DockerWorker._render_snapshot(snapshot, mode)
        if snapshot['daemon_error']:
            return False
        if not DockerWorker._snapshot_ready(snapshot):
            console.print(
                f"[red]❌ {snapshot['healthy']}/{snapshot['expected']} selected Docker services are ready.[/red] "
                'Use docker compose logs for the failing service.'
            )
            return False

        acceptance_errors = DockerWorker._acceptance_errors(mode)
        if acceptance_errors:
            for error in acceptance_errors:
                console.print(f'[red]❌ {error}[/red]')
            return False

        console.print(
            f"[green]✅ {snapshot['healthy']}/{snapshot['expected']} selected Docker services and "
            f'{DockerWorker._mode_label(mode)} acceptance checks are ready.[/green]'
        )
        return True

    @staticmethod
    def build_images(images, local=False, train=None):
        """Build the given images in order. Returns a process exit code.

        With local=True the jar is staged from the checkout before each image that carries one, and
        cleared afterwards: a staged jar is an input to one build, not a mode the tree stays in.
        Staging is strict, so a target whose jar has not been built fails rather than quietly
        falling back to the published one.
        """
        from org.metadatacenter.util.DockerImages import DockerImages

        try:
            environment = os.environ.copy()
            if train:
                environment['CEDAR_TRAIN_VERSION'] = train
            _, version, prefix = DockerImages.manifest(environment)
            base_prefix = DockerImages.base_image_prefix(environment)
        except ValueError as error:
            console.print(f'[red]Build configuration is invalid: {error}[/red]')
            return 1
        build_home = DockerImages.build_home()

        # The locked server versions travel from the manifest into every build as build arguments.
        # Passing them to all images rather than working out which image wants which is deliberate:
        # Docker ignores a build argument a Dockerfile does not declare, and the alternative is a
        # second place recording which image installs which server.
        server_versions = DockerImages.server_versions()
        if train:
            server_versions['CEDAR_MAVEN_VERSION'] = train
        build_args = ' '.join([
            f'--build-arg CEDAR_IMAGE_PREFIX="{base_prefix}"',
            f'--build-arg CEDAR_DOCKER_VERSION="{version}"',
        ] + [
            f'--build-arg {name}="{value}"' for name, value in sorted(server_versions.items())
        ])

        source_revision = DockerImages.source_revision()
        source_manifest = environment.get('CEDAR_TRAIN_MANIFEST_SHA256')
        if source_manifest and not re.fullmatch(r'[0-9a-f]{64}', source_manifest):
            console.print('[red]CEDAR_TRAIN_MANIFEST_SHA256 must be a lowercase SHA-256 digest.[/red]')
            return 1
        steps = []
        for image in images:
            stage = local and DockerImages.stageable(image)
            reference = DockerImages.reference(image, version, environment)
            labels = [
                f'--label org.opencontainers.image.source="https://github.com/metadatacenter/cedar-docker-build"',
                f'--label org.opencontainers.image.version="{version}"',
                f'--label org.metadatacenter.cedar.image="{image}"',
            ]
            if train:
                labels.append(f'--label org.metadatacenter.cedar.train="{train}"')
            if source_revision:
                labels.append(f'--label org.opencontainers.image.revision="{source_revision}"')
            if source_manifest:
                labels.append(
                    '--label org.metadatacenter.cedar.source-manifest-sha256='
                    f'"{source_manifest}"'
                )
            steps.append(f"""
echo "==> {image}"
{f'"{build_home}/bin/stage-local-jar.sh" {image} || exit 1' if stage else ''}
docker build {build_args} {' '.join(labels)} -t "{reference}" "{build_home}/{image}"
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
        if out.returncode != 0:
            return out.returncode
        return 0 if any("All requested images built." in line for line in out) else 1

    @staticmethod
    def create_network():
        output = Worker.execute_generic_shell_commands([
            """
set -e
echo 'Checking previous Docker network ...'
if docker network inspect cedarnet > /dev/null 2>&1
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
        return output.returncode

    @staticmethod
    def create_certificates_volume():
        output = Worker.execute_generic_shell_commands([
            """
set -e
echo 'Creating volumes for TLS certificates and the CEDAR CA...'
docker volume create cedar_cert
docker volume create cedar_ca
"""
        ],
            title="Creating CEDAR certificate volumes",
        )
        return output.returncode

    @staticmethod
    def copy_certificates():
        output = Worker.execute_generic_shell_commands([
            """
set -e
docker rm -f cedar-cert-helper cedar-ca-helper > /dev/null 2>&1 || true
echo "Copying self-signed certificates into the cedar_cert volume..."
docker run -v cedar_cert:/data --name cedar-cert-helper busybox:1.36.0 true
export CEDAR_CUSTOM_CERT=false
if [[ -e "${CEDAR_HOME}/CEDAR_CA/certs/-${CEDAR_HOST}/${CEDAR_HOST}.crt" ]]; then export CEDAR_CUSTOM_CERT=true; fi
if [[ $CEDAR_CUSTOM_CERT == 'true' ]]; then docker cp "${CEDAR_HOME}/CEDAR_CA/certs" cedar-cert-helper:/data; fi
if [[ $CEDAR_CUSTOM_CERT != 'true' ]]; then docker cp "${CEDAR_HOME}/cedar-docker-deploy/cedar-assets/cert/certs" cedar-cert-helper:/data; fi
docker rm cedar-cert-helper

echo "Copying CA certificate into the cedar_ca volume..."
docker run -v cedar_ca:/data --name cedar-ca-helper busybox:1.36.0 true
if [[ $CEDAR_CUSTOM_CERT == 'true' ]]; then docker cp "${CEDAR_HOME}/CEDAR_CA/ca.crt" cedar-ca-helper:/data; fi
if [[ $CEDAR_CUSTOM_CERT != 'true' ]]; then docker cp "${CEDAR_HOME}/cedar-docker-deploy/cedar-assets/ca/ca.crt" cedar-ca-helper:/data; fi
docker rm cedar-ca-helper
"""
        ],
            title="Copy CEDAR self-signed certificates",
        )
        return output.returncode

    @staticmethod
    def remove_containers():
        from org.metadatacenter.util.DockerImages import DockerImages

        try:
            prefix = DockerImages.image_prefix()
        except ValueError as error:
            console.print(f'[red]Removal configuration is invalid: {error}[/red]')
            return 1
        output = Worker.execute_generic_shell_commands([
            f"""
ids=$(
    docker ps -a --format '{{{{.ID}}}} {{{{.Image}}}}' |
        awk -v prefix="{prefix}/cedar-" 'index($2, prefix) == 1 {{print $1}}'
)
if [ -z "${{ids}}" ]; then
    echo 'No CEDAR containers found.'
    exit 0
fi
docker rm -f ${{ids}}
"""
        ],
            title="Removing all CEDAR containers",
        )
        if output.returncode == 0:
            DockerWorker._clear_active_deployment()
        return output.returncode

    @staticmethod
    def remove_images():
        from org.metadatacenter.util.DockerImages import DockerImages

        try:
            prefix = DockerImages.image_prefix()
            base_prefix = DockerImages.base_image_prefix()
        except ValueError as error:
            console.print(f'[red]Removal configuration is invalid: {error}[/red]')
            return 1
        output = Worker.execute_generic_shell_commands([
            f"""
ids=$(
    docker images --format '{{{{.Repository}}}} {{{{.ID}}}}' |
        awk -v prefix="{prefix}/cedar-" -v base="{base_prefix}/cedar-" \
            'index($1, prefix) == 1 || index($1, base) == 1 {{print $2}}' |
        sort -u
)
if [ -z "${{ids}}" ]; then
    echo 'No CEDAR images found.'
    exit 0
fi
docker rmi ${{ids}}
"""
        ],
            title="Removing all CEDAR images",
        )
        return output.returncode

    @staticmethod
    def remove_network():
        output = Worker.execute_generic_shell_commands([
            """
if docker network inspect cedarnet > /dev/null 2>&1; then
    docker network rm cedarnet
else
    echo 'CEDAR network is already absent.'
fi
"""
        ],
            title="Removing CEDAR network",
        )
        return output.returncode

    @staticmethod
    def remove_volumes():
        output = Worker.execute_generic_shell_commands([
            """
failed=0
for volume in \
    cedar_ca cedar_cert \
    opensearch_data log_opensearch \
    keycloak_state log_keycloak \
    mongo_data mongo_state mongo_configdb log_mongo \
    mysql_data log_mysql \
    neo4j_data neo4j_state log_neo4j \
    log_nginx redis_data log_redis \
    terminology_data resource_state \
    log_group log_impex log_monitor log_messaging log_openview log_repo log_resource \
    log_schema log_submission log_artifact log_terminology log_user \
    log_valuerecommender log_worker log_bridge \
    log_frontend_main log_frontend_openview log_frontend_content \
    log_frontend_monitoring log_frontend_bridging log_frontend_workspace \
    log_frontend_template_designer
do
    if docker volume inspect "${volume}" > /dev/null 2>&1; then
        docker volume rm "${volume}" || failed=1
    else
        echo "Already absent: ${volume}"
    fi
done
exit ${failed}
"""
        ],
            title="Removing all CEDAR volumes",
        )
        return output.returncode

    # Stack name in cedar-docker-deploy, and what to call it when talking to the user.
    STACKS = {
        'infrastructure': ('cedar-infrastructure', 'infrastructure services'),
        'microservices': ('cedar-microservices', 'microservices'),
        'frontends': ('cedar-frontend', 'frontends'),
        'admin': ('cedar-admin', 'admin tools'),
    }

    @staticmethod
    def _stack_directory(stack):
        directory, _ = DockerWorker.STACKS[stack]
        return os.path.join(Util.cedar_home, 'cedar-docker-deploy', directory)

    @staticmethod
    def _published_ports(stack_names, environment):
        ports = []
        errors = []
        for stack in stack_names:
            result = DockerWorker._docker_command(
                ['compose', 'config', '--format', 'json'],
                cwd=DockerWorker._stack_directory(stack),
                environment=environment,
            )
            if result.returncode != 0:
                errors.append(result.stderr.strip() or f'could not resolve the {stack} Compose project')
                continue
            try:
                model = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                errors.append(f'could not read published ports for {stack}: {error}')
                continue
            for service in (model.get('services') or {}).values():
                for port in service.get('ports') or []:
                    published = port.get('published') if isinstance(port, dict) else None
                    if published is not None:
                        try:
                            ports.append(int(published))
                        except (TypeError, ValueError):
                            errors.append(f'{stack} has an invalid published port: {published}')
        return sorted(set(ports)), errors

    @staticmethod
    def _port_has_listener(port):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.15):
                return True
        except OSError:
            return False

    @staticmethod
    def _port_owned_by_selected_compose_project(port, stack_names):
        result = DockerWorker._docker_command([
            'ps', '--filter', f'publish={port}',
            '--format', '{{.Label "com.docker.compose.project"}}',
        ])
        if result.returncode != 0:
            return False
        allowed_projects = {DockerWorker.STACKS[stack][0] for stack in stack_names}
        projects = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return bool(projects) and projects.issubset(allowed_projects)

    @staticmethod
    def preflight(mode, environment):
        """Fail before creating containers when the selected deployment cannot start safely."""
        errors = []
        if DockerWorker.validate(environment=environment) != 0:
            return False

        _, daemon_error = DockerWorker._docker_server_version()
        if daemon_error:
            errors.append(daemon_error)

        for resource_type, resource_names in (
                ('network', ('cedarnet',)),
                ('volume', ('cedar_cert', 'cedar_ca'))):
            for resource_name in resource_names:
                result = DockerWorker._docker_command([resource_type, 'inspect', resource_name])
                if result.returncode != 0:
                    errors.append(
                        f'Docker {resource_type} {resource_name} is missing; '
                        'run cedarcli docker one-time-setup'
                    )

        stack_names = DockerWorker._stack_names(mode)
        ports, port_errors = DockerWorker._published_ports(stack_names, environment)
        errors.extend(port_errors)
        for port in ports:
            if (
                    DockerWorker._port_has_listener(port)
                    and not DockerWorker._port_owned_by_selected_compose_project(port, stack_names)):
                errors.append(f'host port {port} is already used outside the selected Docker deployment')

        if errors:
            console.print('[red]Docker deployment preflight failed:[/red]')
            for error in errors:
                console.print(f'  ❌ {error}')
            return False
        console.print(
            f'[green]✅ Docker preflight passed for {DockerWorker._mode_label(mode)} mode.[/green]'
        )
        return True

    @staticmethod
    def _print_failure_logs(snapshot, environment):
        failures_by_stack = {}
        for stack, service, indicator, _container, _detail in snapshot['rows']:
            if indicator != '✅' and service != 'Compose project':
                failures_by_stack.setdefault(stack, []).append(service)
        for stack, services in failures_by_stack.items():
            result = DockerWorker._docker_command(
                ['compose', 'logs', '--tail', '100', *services],
                cwd=DockerWorker._stack_directory(stack),
                environment=environment,
            )
            console.print(f'[yellow]Recent {stack} logs ({", ".join(services)}):[/yellow]')
            output = (result.stdout + result.stderr).strip()
            console.print(output or '(no logs returned)', markup=False)

    @staticmethod
    def _wait_for_stacks(stack_names, deadline, mode, environment):
        last_progress = None
        snapshot = None
        while True:
            snapshot = DockerWorker._container_snapshot(stack_names, environment=environment)
            progress = (snapshot['healthy'], snapshot['expected'])
            if progress != last_progress:
                console.print(
                    f'Waiting for {DockerWorker._mode_label(mode)}: '
                    f'{progress[0]}/{progress[1]} containers ready'
                )
                last_progress = progress
            if DockerWorker._snapshot_ready(snapshot):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                DockerWorker._render_snapshot(snapshot, mode)
                DockerWorker._print_failure_logs(snapshot, environment)
                return False
            time.sleep(min(2, remaining))

    @staticmethod
    def _wait_for_acceptance(mode, deadline):
        errors = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            check_count = 1 + (len(FRONTEND_PUBLIC_HOSTS) if mode.checks_frontend_routes else 0)
            probe_timeout = max(1, min(5, remaining / check_count))
            errors = DockerWorker._acceptance_errors(mode, timeout=probe_timeout)
            if not errors:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(2, remaining))
        console.print(
            f'[red]{DockerWorker._mode_label(mode)} acceptance checks did not become ready:[/red]'
        )
        for error in errors:
            console.print(f'  ❌ {error}')
        return False

    @staticmethod
    def start_all(mode, pull='never', timeout=600, train=None):
        if isinstance(mode, str):
            mode = DockerDeploymentMode(mode)
        environment, environment_errors = DockerWorker.mode_environment(mode)
        if train:
            environment['CEDAR_DOCKER_VERSION'] = train
        if environment_errors:
            for error in environment_errors:
                console.print(f'[red]❌ {error}[/red]')
            return 1
        if not DockerWorker.preflight(mode, environment):
            return 1

        deadline = time.monotonic() + timeout
        if not mode.includes_frontend_containers:
            if DockerWorker.compose('frontends', 'down', environment=environment) != 0:
                return 1

        started_stacks = []
        requested_stacks = DockerWorker._stack_names(mode)
        for stack in requested_stacks:
            if DockerWorker.compose(stack, 'up', detach=True, pull=pull, environment=environment) != 0:
                return 1
            started_stacks.append(stack)
            if not DockerWorker._wait_for_stacks(list(started_stacks), deadline, mode, environment):
                return 1

        if not DockerWorker._wait_for_acceptance(mode, deadline):
            return 1

        try:
            if train:
                DockerWorker._record_active_deployment(mode, train=train)
            else:
                DockerWorker._record_active_deployment(mode)
        except OSError as error:
            console.print(
                '[red]Containers are ready, but the active deployment mode could not be '
                f'recorded: {error}[/red]'
            )
            return 1
        qualifier = '' if mode is DockerDeploymentMode.FULL else ' hybrid'
        console.print(f'[green]✅ CEDAR Docker{qualifier} deployment is ready.[/green]')
        return 0

    @staticmethod
    def stop_all(mode=None):
        _version, daemon_error = DockerWorker._docker_server_version()
        if daemon_error:
            console.print(
                "[red]Docker is unavailable; no Compose project was changed.[/red]\n"
                "Start Docker and retry, or use cedarcli mode --clear --force if Docker "
                "has deliberately been shut down."
            )
            return 1
        mode = mode or DockerWorker.active_deployment() or DockerDeploymentMode.FULL
        if isinstance(mode, str):
            mode = DockerDeploymentMode(mode)
        environment, errors = DockerWorker.mode_environment(mode)
        if errors:
            environment = os.environ.copy()

        stacks = ['frontends', 'microservices', 'infrastructure']
        first_failure = 0
        for stack in stacks:
            returncode = DockerWorker.compose(stack, 'down', environment=environment)
            if returncode and not first_failure:
                first_failure = returncode
        if first_failure == 0:
            DockerWorker._clear_active_deployment()
        return first_failure

    @staticmethod
    def compose(stack, action, detach=False, pull=None, environment=None, services=()):
        if environment is None:
            active_mode = DockerWorker.active_deployment()
            if active_mode is not None:
                active_environment, errors = DockerWorker.mode_environment(active_mode)
                if not errors:
                    environment = active_environment
                    active_train = DockerWorker.active_train()
                    if active_train:
                        environment['CEDAR_DOCKER_VERSION'] = active_train
        directory, label = DockerWorker.STACKS[stack]
        command = 'docker compose ' + action
        if action == 'up' and detach:
            command += ' --detach'
        if action == 'up' and pull:
            command += f' --pull {pull}'
        if services:
            command += ' ' + ' '.join(shlex.quote(service) for service in services)
        output = Worker.execute_generic_shell_commands(
            [command],
            title=("Starting" if action == 'up' else "Stopping") + " CEDAR " + label,
            cwd=os.path.join(Util.cedar_home, 'cedar-docker-deploy', directory),
            env=environment,
        )
        return output.returncode

    @staticmethod
    def _individual_start(stack, detach=False, pull='never', train=None):
        environment = os.environ.copy()
        if train:
            environment['CEDAR_DOCKER_VERSION'] = train
        return DockerWorker.compose(stack, 'up', detach, pull, environment=environment)

    @staticmethod
    def _individual_service_start(stack, service, detach=False, pull='never', train=None):
        environment = os.environ.copy()
        if train:
            environment['CEDAR_DOCKER_VERSION'] = train
        return DockerWorker.compose(
            stack,
            'up',
            detach,
            pull,
            environment=environment,
            services=(service,),
        )

    @staticmethod
    def start_infrastructure(detach=False, pull='never', train=None):
        return DockerWorker._individual_start('infrastructure', detach, pull, train)

    @staticmethod
    def start_keycloak(detach=False, pull='never', train=None):
        return DockerWorker._individual_service_start(
            'infrastructure', 'keycloak', detach, pull, train)

    @staticmethod
    def start_microservices(detach=False, pull='never', train=None):
        return DockerWorker._individual_start('microservices', detach, pull, train)

    @staticmethod
    def start_microservice(microservice, detach=False, pull='never', train=None):
        if microservice == 'all':
            return DockerWorker.start_microservices(detach, pull, train)
        return DockerWorker._individual_service_start(
            'microservices',
            MICROSERVICE_COMPOSE_SERVICES[microservice],
            detach,
            pull,
            train,
        )

    @staticmethod
    def start_frontends(detach=False, pull='never', train=None):
        active_mode = DockerWorker.active_deployment()
        if active_mode is not None and not active_mode.includes_frontend_containers:
            console.print(
                f'[red]The active Docker deployment is {active_mode.value}; stop it, clear '
                'the configured CEDAR mode, and select docker before starting Docker frontends.[/red]'
            )
            return 1
        return DockerWorker._individual_start('frontends', detach, pull, train)

    @staticmethod
    def start_frontend(frontend, detach=False, pull='never', train=None):
        if frontend == 'all':
            return DockerWorker.start_frontends(detach, pull, train)
        active_mode = DockerWorker.active_deployment()
        if active_mode is not None and not active_mode.includes_frontend_containers:
            console.print(
                f'[red]The active Docker deployment is {active_mode.value}; stop it, clear '
                'the configured CEDAR mode, and select docker before starting Docker frontends.[/red]'
            )
            return 1
        return DockerWorker._individual_service_start(
            'frontends',
            FRONTEND_COMPOSE_SERVICES[frontend],
            detach,
            pull,
            train,
        )

    @staticmethod
    def start_admin(detach=False, pull='never', train=None):
        return DockerWorker._individual_start('admin', detach, pull, train)

    @staticmethod
    def stop_infrastructure():
        return DockerWorker.compose('infrastructure', 'down')

    @staticmethod
    def stop_keycloak():
        return DockerWorker.compose('infrastructure', 'stop', services=('keycloak',))

    @staticmethod
    def stop_microservices():
        return DockerWorker.compose('microservices', 'down')

    @staticmethod
    def stop_microservice(microservice):
        if microservice == 'all':
            return DockerWorker.stop_microservices()
        return DockerWorker.compose(
            'microservices',
            'stop',
            services=(MICROSERVICE_COMPOSE_SERVICES[microservice],),
        )

    @staticmethod
    def stop_frontends():
        return DockerWorker.compose('frontends', 'down')

    @staticmethod
    def stop_frontend(frontend):
        if frontend == 'all':
            return DockerWorker.stop_frontends()
        return DockerWorker.compose(
            'frontends',
            'stop',
            services=(FRONTEND_COMPOSE_SERVICES[frontend],),
        )

    @staticmethod
    def stop_admin():
        return DockerWorker.compose('admin', 'down')

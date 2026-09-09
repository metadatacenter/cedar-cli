"""CEDAR docker setup."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.worker.CertificateWorker import CertificateError, CertificateWorker
from org.metadatacenter.worker.Worker import Worker
import os
from org.metadatacenter.docker_support import output as _output_component
from org.metadatacenter.docker_support import state as _state_component


def validate(environment=None):
    from org.metadatacenter.util.DockerImages import DockerImages

    try:
        prefix = DockerImages.image_prefix(environment)
        base_prefix = DockerImages.base_image_prefix(environment)
    except ValueError as error:
        _output_component.console.print(f'[red]FAIL Docker image configuration: {error}[/red]')
        return 1

    validation_environment = (invocation_environment() if environment is None else environment).copy()
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


def copy_certificates():
    try:
        returncode = CertificateWorker.ensure_ca_and_domains()
    except CertificateError as error:
        _output_component.console.print(f'[red]{error}[/red]')
        return 1
    if returncode:
        return returncode

    output = Worker.execute_generic_shell_commands([
        """
set -e
docker rm -f cedar-cert-helper cedar-ca-helper > /dev/null 2>&1 || true
echo "Copying locally generated certificates into the cedar_cert volume..."
docker run -v cedar_cert:/data --name cedar-cert-helper busybox:1.36.0 true
docker cp "${CEDAR_CA_HOME}/certs" cedar-cert-helper:/data
docker rm cedar-cert-helper

echo "Copying CA certificate into the cedar_ca volume..."
docker run -v cedar_ca:/data --name cedar-ca-helper busybox:1.36.0 true
docker cp "${CEDAR_CA_HOME}/ca.crt" cedar-ca-helper:/data
docker rm cedar-ca-helper
"""
    ],
        title="Copy locally generated CEDAR certificates",
    )
    return output.returncode


def remove_containers():
    from org.metadatacenter.util.DockerImages import DockerImages

    try:
        prefix = DockerImages.image_prefix()
    except ValueError as error:
        _output_component.console.print(f'[red]Removal configuration is invalid: {error}[/red]')
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
        _state_component._clear_active_deployment()
    return output.returncode


def remove_images():
    from org.metadatacenter.util.DockerImages import DockerImages

    try:
        prefix = DockerImages.image_prefix()
        base_prefix = DockerImages.base_image_prefix()
    except ValueError as error:
        _output_component.console.print(f'[red]Removal configuration is invalid: {error}[/red]')
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

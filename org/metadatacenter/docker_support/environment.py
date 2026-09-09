"""CEDAR docker environment."""
from __future__ import annotations
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
import os
from org.metadatacenter.docker_support import policy as _policy_component


def _stack_names(mode):
    names = ['infrastructure', 'microservices']
    if mode.includes_frontend_containers:
        names.append('frontends')
    return names


def _mode_label(mode):
    return 'docker' if mode is DockerDeploymentMode.FULL else mode.value


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
    for frontend in _policy_component.FRONTEND_NAMES:
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

    if len(missing_container_hosts) == len(_policy_component.FRONTEND_NAMES):
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

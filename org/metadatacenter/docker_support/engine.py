"""CEDAR docker engine."""
from __future__ import annotations
from org.metadatacenter.util.Util import Util
import json
import os
import subprocess
from org.metadatacenter.docker_support import policy as _policy_component


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


def _docker_server_version():
    result = _docker_command(['info', '--format', '{{.ServerVersion}}'])
    if result.returncode != 0:
        return None, result.stderr.strip() or 'Docker daemon is unavailable'
    return result.stdout.strip(), None


def _expected_compose_services(stack_directory, environment=None):
    result = _docker_command(
        ['compose', 'config', '--no-interpolate', '--services'],
        cwd=stack_directory,
        environment=environment,
    )
    if result.returncode != 0:
        return [], result.stderr.strip() or 'Unable to read the Compose project'
    return [line for line in result.stdout.splitlines() if line], None


def _compose_containers(project_name):
    result = _docker_command([
        'ps', '-aq',
        '--filter', f'label=com.docker.compose.project={project_name}',
    ])
    if result.returncode != 0:
        return {}, result.stderr.strip() or 'Unable to list Docker containers'

    container_ids = result.stdout.split()
    if not container_ids:
        return {}, None

    result = _docker_command(['inspect', *container_ids])
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


def running_compose_projects():
    """Return running CEDAR Compose projects; daemon absence means none are running."""
    result = _docker_command([
        'ps', '--format', '{{.Label "com.docker.compose.project"}}',
    ])
    if result.returncode != 0:
        return set()
    known = {directory for directory, _label in _policy_component.STACKS.values()}
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() in known
    }


def _stack_directory(stack):
    directory, _ = _policy_component.STACKS[stack]
    return os.path.join(Util.cedar_home, 'cedar-docker-deploy', directory)


def _published_ports(stack_names, environment):
    ports = []
    errors = []
    for stack in stack_names:
        result = _docker_command(
            ['compose', 'config', '--format', 'json'],
            cwd=_stack_directory(stack),
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


def _port_owned_by_selected_compose_project(port, stack_names):
    result = _docker_command([
        'ps', '--filter', f'publish={port}',
        '--format', '{{.Label "com.docker.compose.project"}}',
    ])
    if result.returncode != 0:
        return False
    allowed_projects = {_policy_component.STACKS[stack][0] for stack in stack_names}
    projects = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return bool(projects) and projects.issubset(allowed_projects)

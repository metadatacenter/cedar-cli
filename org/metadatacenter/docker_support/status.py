"""CEDAR docker status."""
from __future__ import annotations
from org.metadatacenter.docker_support import state as _state_component
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.util.DockerImages import DockerImages
from org.metadatacenter.util.Util import Util
from rich import box
from rich.table import Table
from rich.text import Text
import os
import re
from org.metadatacenter.docker_support import acceptance as _acceptance_component
from org.metadatacenter.docker_support import engine as _engine_component
from org.metadatacenter.docker_support import environment as _environment_component
from org.metadatacenter.docker_support import images as _images_component
from org.metadatacenter.docker_support import output as _output_component
from org.metadatacenter.docker_support import policy as _policy_component


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


def _container_ports(container):
    if container is None:
        return '—'
    bindings = (container.get('NetworkSettings') or {}).get('Ports') or {}
    ports = []
    for container_port, published in bindings.items():
        internal = container_port.split('/', 1)[0]
        if published:
            for binding in published:
                host = binding.get('HostPort')
                if not host:
                    continue
                ports.append(host if host == internal else f'{host}→{internal}')
        else:
            ports.append(f'{internal} int')
    if not ports:
        exposed = (container.get('Config') or {}).get('ExposedPorts') or {}
        ports.extend(f"{port.split('/', 1)[0]} int" for port in exposed)
    return ','.join(sorted(set(ports), key=_port_sort_key)) or '—'


def _port_sort_key(value):
    match = re.match(r'(\d+)', value)
    return (int(match.group(1)) if match else 99999, value)


def _container_image_status(stack_name, service, container, environment):
    if container is None:
        return '—', None
    version = environment.get('CEDAR_DOCKER_VERSION') if environment else None
    if not version:
        return 'unknown', None
    image_name = _images_component._train_image_names(stack_name, (service,))[0]
    try:
        expected = DockerImages.reference(image_name, version, environment)
    except ValueError as error:
        return 'unknown', f'could not determine the expected image: {error}'
    actual = (container.get('Config') or {}).get('Image')
    if actual == expected:
        return 'current', None
    return 'MISMATCH', f'running image {actual or "unknown"}; expected {expected}'


def _container_snapshot(stack_names, environment=None):
    server_version, daemon_error = _engine_component._docker_server_version()
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
        directory, _ = _policy_component.STACKS[stack_name]
        stack_directory = os.path.join(Util.cedar_home, 'cedar-docker-deploy', directory)
        services, compose_error = _engine_component._expected_compose_services(
            stack_directory,
            environment=environment,
        )
        services = _ordered_status_services(stack_name, services)

        if compose_error:
            snapshot['expected'] += 1
            snapshot['rows'].append(
                (stack_name, 'Compose project', '❌', '', compose_error, '—', '—', '—'))
            continue
        if not services:
            snapshot['expected'] += 1
            snapshot['rows'].append(
                (stack_name, 'Compose project', '❌', '', 'no services defined', '—', '—', '—'))
            continue

        containers, container_error = _engine_component._compose_containers(directory)
        if container_error:
            snapshot['expected'] += len(services)
            for service in services:
                snapshot['rows'].append(
                    (stack_name, service, '❌', '', container_error, '—', '—', '—'))
            continue

        for service in services:
            snapshot['expected'] += 1
            container = containers.get(service)
            indicator, container_name, detail = _container_report(container)
            image_status, image_error = _container_image_status(
                stack_name, service, container, environment)
            if image_error:
                detail = f'{detail}; {image_error}'
            if image_status == 'MISMATCH':
                indicator = '❌'
            if indicator == '✅':
                snapshot['healthy'] += 1
            snapshot['rows'].append((
                stack_name, service, indicator, container_name, detail,
                image_status, _container_ports(container),
                str(container.get('RestartCount', 0)) if container else '—',
            ))

    return snapshot


def _ordered_status_services(stack_name, services):
    """Use a stable human-facing order and retain unknown future services at the end."""
    preferred = _policy_component.STATUS_SERVICE_ORDER.get(stack_name, ())
    rank = {service: position for position, service in enumerate(preferred)}
    return sorted(
        services,
        key=lambda service: (rank.get(service, len(preferred)), service),
    )


def _snapshot_ready(snapshot):
    return (
        snapshot['daemon_error'] is None
        and snapshot['expected'] > 0
        and snapshot['healthy'] == snapshot['expected']
    )


def _render_snapshot(snapshot, mode):
    if snapshot['daemon_error']:
        _output_component.console.print(
            "[red]❌ Docker is unavailable.[/red] Start Docker Desktop or repair "
            "Docker daemon access, then retry."
        )
        return

    table = Table(
        'Service', 'Health', 'Image', 'Ports', 'Restarts',
        title=(f"CEDAR Docker status: {_environment_component._mode_label(mode)} "
               f"(Engine {snapshot['server_version']})"),
        box=box.SIMPLE_HEAVY, header_style='bold', show_edge=False,
        pad_edge=False,
    )
    table.columns[4].justify = 'right'
    previous_stack = None
    labels = {
        'infrastructure': 'Infrastructure',
        'microservices': 'Microservices',
        'frontends': 'Frontends',
        'admin': 'Administration',
    }
    for stack, service, indicator, _container, detail, image, ports, restarts in snapshot['rows']:
        if stack != previous_stack:
            if table.row_count:
                table.add_section()
            table.add_row(Text(labels.get(stack, stack.capitalize()), style='bold magenta'), '', '', '', '')
            previous_stack = stack
        table.add_row(
            service,
            _health_text(detail),
            _image_text(image),
            Text(ports, style='dim' if ports == '—' else ''),
            Text(restarts, style='yellow' if restarts not in {'0', '—'} else 'dim'),
        )
    _output_component.console.print(table)
    for _stack, service, indicator, _container, detail, _image, _ports, _restarts in snapshot['rows']:
        if indicator != '✅':
            _output_component.console.print(Text(f'WARNING  {service}: {detail}', style='yellow'))


def _health_text(detail):
    if detail == 'healthy':
        return Text('healthy', style='green')
    if detail == 'running (no healthcheck)':
        return Text('running', style='green')
    if detail == 'healthcheck starting':
        return Text('starting', style='yellow')
    if detail.startswith('healthy;'):
        return Text('healthy', style='green')
    if detail.startswith('running (no healthcheck);'):
        return Text('running', style='green')
    if detail == 'missing':
        return Text('missing', style='bold red')
    state = detail.split(':', 1)[0].split(';', 1)[0]
    return Text(state, style='bold red')


def _image_text(value):
    if value == 'current':
        return Text(value, style='green')
    if value == 'MISMATCH':
        return Text(value, style='bold red')
    if value in {'—', 'unknown'}:
        return Text(value, style='dim' if value == '—' else 'yellow')
    return Text(value)


def status(mode=DockerDeploymentMode.FULL):
    """Report container health and the acceptance checks selected by the deployment mode."""
    if isinstance(mode, str):
        mode = DockerDeploymentMode(mode)

    environment, environment_errors = _environment_component.mode_environment(mode)
    active_train = _state_component.active_train()
    if active_train:
        environment['CEDAR_DOCKER_VERSION'] = active_train
    if environment_errors:
        for error in environment_errors:
            _output_component.console.print(f'[red]❌ {error}[/red]')
        return False

    snapshot = _container_snapshot(
        _environment_component._stack_names(mode),
        environment=environment,
    )
    _render_snapshot(snapshot, mode)
    if snapshot['daemon_error']:
        return False
    image_set = active_train or environment.get('CEDAR_DOCKER_VERSION') or 'unverified local tag'
    if not _snapshot_ready(snapshot):
        _output_component.console.print(
            f"[red]Summary  {snapshot['healthy']}/{snapshot['expected']} containers ready"
            f"  •  acceptance not run  •  image set {image_set}[/red]"
        )
        _output_component.console.print('[dim]Use docker compose logs for a failing service.[/dim]')
        return False

    acceptance_errors = _acceptance_component._acceptance_errors(mode)
    if acceptance_errors:
        for error in acceptance_errors:
            _output_component.console.print(f'[red]WARNING  {error}[/red]')
        _output_component.console.print(
            f"[red]Summary  {snapshot['healthy']}/{snapshot['expected']} containers ready"
            f"  •  acceptance failed  •  image set {image_set}[/red]"
        )
        return False

    acceptance_count = 1 + (
        len(_policy_component.FRONTEND_PUBLIC_HOSTS) if mode.checks_frontend_routes else 0)
    _output_component.console.print(
        f"[green]Summary  {snapshot['healthy']}/{snapshot['expected']} containers ready"
        f"  •  {acceptance_count}/{acceptance_count} acceptance checks ready"
        f"  •  image set {image_set}[/green]"
    )
    return True

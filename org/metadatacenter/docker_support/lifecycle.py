"""CEDAR docker lifecycle."""
from __future__ import annotations
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.util.DockerImages import DockerImages
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker
import os
import shlex
import socket
import time
from org.metadatacenter.docker_support import acceptance as _acceptance_component
from org.metadatacenter.docker_support import engine as _engine_component
from org.metadatacenter.docker_support import environment as _environment_component
from org.metadatacenter.docker_support import images as _images_component
from org.metadatacenter.docker_support import output as _output_component
from org.metadatacenter.docker_support import policy as _policy_component
from org.metadatacenter.docker_support import setup as _setup_component
from org.metadatacenter.docker_support import state as _state_component
from org.metadatacenter.docker_support import status as _status_component


def _port_has_listener(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.15):
            return True
    except OSError:
        return False


def preflight(mode, environment):
    """Fail before creating containers when the selected deployment cannot start safely."""
    errors = []
    if _setup_component.validate(environment=environment) != 0:
        return False

    _, daemon_error = _engine_component._docker_server_version()
    if daemon_error:
        errors.append(daemon_error)

    for resource_type, resource_names in (
            ('network', ('cedarnet',)),
            ('volume', ('cedar_cert', 'cedar_ca'))):
        for resource_name in resource_names:
            result = _engine_component._docker_command([resource_type, 'inspect', resource_name])
            if result.returncode != 0:
                errors.append(
                    f'Docker {resource_type} {resource_name} is missing; '
                    'run cedarcli docker setup one-time-setup'
                )

    stack_names = _environment_component._stack_names(mode)
    ports, port_errors = _engine_component._published_ports(stack_names, environment)
    errors.extend(port_errors)
    for port in ports:
        if (
                _port_has_listener(port)
                and not _engine_component._port_owned_by_selected_compose_project(port, stack_names)):
            errors.append(f'host port {port} is already used outside the selected Docker deployment')

    if errors:
        _output_component.console.print('[red]Docker deployment preflight failed:[/red]')
        for error in errors:
            _output_component.console.print(f'  ❌ {error}')
        return False
    _output_component.console.print(
        f'[green]✅ Docker preflight passed for {_environment_component._mode_label(mode)} mode.[/green]'
    )
    return True


def _print_failure_logs(snapshot, environment):
    failures_by_stack = {}
    for stack, service, indicator, _container, _detail, _image, _ports, _restarts in snapshot['rows']:
        if indicator != '✅' and service != 'Compose project':
            failures_by_stack.setdefault(stack, []).append(service)
    for stack, services in failures_by_stack.items():
        result = _engine_component._docker_command(
            ['compose', 'logs', '--tail', '100', *services],
            cwd=_engine_component._stack_directory(stack),
            environment=environment,
        )
        _output_component.console.print(f'[yellow]Recent {stack} logs ({", ".join(services)}):[/yellow]')
        output = (result.stdout + result.stderr).strip()
        _output_component.console.print(output or '(no logs returned)', markup=False)


def _wait_for_stacks(stack_names, deadline, mode, environment):
    last_progress = None
    snapshot = None
    while True:
        snapshot = _status_component._container_snapshot(stack_names, environment=environment)
        progress = (snapshot['healthy'], snapshot['expected'])
        if progress != last_progress:
            _output_component.console.print(
                f'Waiting for {_environment_component._mode_label(mode)}: '
                f'{progress[0]}/{progress[1]} containers ready'
            )
            last_progress = progress
        if _status_component._snapshot_ready(snapshot):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _status_component._render_snapshot(snapshot, mode)
            _print_failure_logs(snapshot, environment)
            return False
        time.sleep(min(2, remaining))


def _wait_for_acceptance(mode, deadline):
    errors = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        check_count = 1 + (len(_policy_component.FRONTEND_PUBLIC_HOSTS) if mode.checks_frontend_routes else 0)
        probe_timeout = max(1, min(5, remaining / check_count))
        errors = _acceptance_component._acceptance_errors(mode, timeout=probe_timeout)
        if not errors:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(2, remaining))
    _output_component.console.print(
        f'[red]{_environment_component._mode_label(mode)} acceptance checks did not become ready:[/red]'
    )
    for error in errors:
        _output_component.console.print(f'  ❌ {error}')
    return False


def start_all(mode, pull='never', timeout=600, train=None):
    if isinstance(mode, str):
        mode = DockerDeploymentMode(mode)
    environment, environment_errors = _environment_component.mode_environment(mode)
    if train:
        environment['CEDAR_DOCKER_VERSION'] = train
    if environment_errors:
        for error in environment_errors:
            _output_component.console.print(f'[red]❌ {error}[/red]')
        return 1
    if not preflight(mode, environment):
        return 1

    requested_stacks = _environment_component._stack_names(mode)
    compose_pull = pull
    if train:
        if not _images_component._prepare_train_images(
                train, requested_stacks, pull, environment):
            return 1
        # Pulling is complete and the local tags have been matched to the completion record.
        # Do not give Compose a chance to resolve the tags again between verification and start.
        compose_pull = 'never'
    if 'microservices' in requested_stacks:
        artifact_version = train or DockerImages.manifest(environment)[1]
        artifact_reference = DockerImages.reference(
            'cedar-server-artifact', artifact_version, environment)
        if not _images_component._prepare_microservice_volumes(artifact_reference):
            return 1
    if 'frontends' in requested_stacks:
        frontend_version = train or DockerImages.manifest(environment)[1]
        frontend_reference = DockerImages.reference(
            'cedar-frontend-main', frontend_version, environment)
        if not _images_component._prepare_frontend_volumes(frontend_reference):
            return 1

    deadline = time.monotonic() + timeout
    if not mode.includes_frontend_containers:
        if compose('frontends', 'down', environment=environment) != 0:
            return 1

    started_stacks = []
    for stack in requested_stacks:
        if compose(
                stack, 'up', detach=True, pull=compose_pull, environment=environment) != 0:
            return 1
        started_stacks.append(stack)
        if not _wait_for_stacks(list(started_stacks), deadline, mode, environment):
            return 1

    if not _wait_for_acceptance(mode, deadline):
        return 1

    try:
        if train:
            _state_component._record_active_deployment(mode, train=train)
        else:
            _state_component._record_active_deployment(mode)
    except OSError as error:
        _output_component.console.print(
            '[red]Containers are ready, but the active deployment mode could not be '
            f'recorded: {error}[/red]'
        )
        return 1
    qualifier = '' if mode is DockerDeploymentMode.FULL else ' hybrid'
    _output_component.console.print(f'[green]✅ CEDAR Docker{qualifier} deployment is ready.[/green]')
    return 0


def stop_all(mode=None):
    _version, daemon_error = _engine_component._docker_server_version()
    if daemon_error:
        _output_component.console.print(
            "[red]Docker is unavailable; no Compose project was changed.[/red]\n"
            "Start Docker and retry, or use cedarcli mode --clear --force if Docker "
            "has deliberately been shut down."
        )
        return 1
    mode = mode or _state_component.active_deployment() or DockerDeploymentMode.FULL
    if isinstance(mode, str):
        mode = DockerDeploymentMode(mode)
    environment, errors = _environment_component.mode_environment(mode)
    if errors:
        environment = os.environ.copy()

    stacks = ['frontends', 'microservices', 'infrastructure']
    first_failure = 0
    for stack in stacks:
        returncode = compose(stack, 'down', environment=environment)
        if returncode and not first_failure:
            first_failure = returncode
    if first_failure == 0:
        _state_component._clear_active_deployment()
    return first_failure


def compose(stack, action, detach=False, pull=None, environment=None, services=()):
    if environment is None:
        active_mode = _state_component.active_deployment()
        if active_mode is not None:
            active_environment, errors = _environment_component.mode_environment(active_mode)
            if not errors:
                environment = active_environment
                active_train = _state_component.active_train()
                if active_train:
                    environment['CEDAR_DOCKER_VERSION'] = active_train
    directory, label = _policy_component.STACKS[stack]
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


def _individual_start(stack, detach=False, pull='never', train=None):
    environment = os.environ.copy()
    if train:
        environment['CEDAR_DOCKER_VERSION'] = train
        if not _images_component._prepare_train_images(train, [stack], pull, environment):
            return 1
        pull = 'never'
    if stack == 'microservices':
        artifact_version = train or DockerImages.manifest(environment)[1]
        artifact_reference = DockerImages.reference(
            'cedar-server-artifact', artifact_version, environment)
        if not _images_component._prepare_microservice_volumes(artifact_reference):
            return 1
    if stack == 'frontends':
        frontend_version = train or DockerImages.manifest(environment)[1]
        frontend_reference = DockerImages.reference(
            'cedar-frontend-main', frontend_version, environment)
        if not _images_component._prepare_frontend_volumes(frontend_reference):
            return 1
    return compose(stack, 'up', detach, pull, environment=environment)


def _individual_service_start(stack, service, detach=False, pull='never', train=None):
    environment = os.environ.copy()
    if train:
        environment['CEDAR_DOCKER_VERSION'] = train
        if not _images_component._prepare_train_images(
                train, [stack], pull, environment, {stack: (service,)}):
            return 1
        pull = 'never'
    if stack == 'microservices':
        artifact_version = train or DockerImages.manifest(environment)[1]
        artifact_reference = DockerImages.reference(
            f'cedar-{service}', artifact_version, environment)
        if not _images_component._prepare_microservice_volumes(artifact_reference):
            return 1
    if stack == 'frontends':
        frontend_version = train or DockerImages.manifest(environment)[1]
        frontend_reference = DockerImages.reference(
            f'cedar-{service}', frontend_version, environment)
        if not _images_component._prepare_frontend_volumes(frontend_reference):
            return 1
    return compose(
        stack,
        'up',
        detach,
        pull,
        environment=environment,
        services=(service,),
    )


def start_infrastructure(detach=False, pull='never', train=None):
    return _individual_start('infrastructure', detach, pull, train)


def start_keycloak(detach=False, pull='never', train=None):
    return _individual_service_start(
        'infrastructure', 'keycloak', detach, pull, train)


def start_microservices(detach=False, pull='never', train=None):
    return _individual_start('microservices', detach, pull, train)


def start_microservice(microservice, detach=False, pull='never', train=None):
    if microservice == 'all':
        return start_microservices(detach, pull, train)
    return _individual_service_start(
        'microservices',
        _policy_component.MICROSERVICE_COMPOSE_SERVICES[microservice],
        detach,
        pull,
        train,
    )


def start_frontends(detach=False, pull='never', train=None):
    active_mode = _state_component.active_deployment()
    if active_mode is not None and not active_mode.includes_frontend_containers:
        _output_component.console.print(
            f'[red]The active Docker deployment is {active_mode.value}; stop it, clear '
            'the configured CEDAR mode, and select docker before starting Docker frontends.[/red]'
        )
        return 1
    return _individual_start('frontends', detach, pull, train)


def start_frontend(frontend, detach=False, pull='never', train=None):
    if frontend == 'all':
        return start_frontends(detach, pull, train)
    active_mode = _state_component.active_deployment()
    if active_mode is not None and not active_mode.includes_frontend_containers:
        _output_component.console.print(
            f'[red]The active Docker deployment is {active_mode.value}; stop it, clear '
            'the configured CEDAR mode, and select docker before starting Docker frontends.[/red]'
        )
        return 1
    return _individual_service_start(
        'frontends',
        _policy_component.FRONTEND_COMPOSE_SERVICES[frontend],
        detach,
        pull,
        train,
    )


def start_admin(detach=False, pull='never', train=None):
    return _individual_start('admin', detach, pull, train)


def stop_infrastructure():
    return compose('infrastructure', 'down')


def stop_keycloak():
    return compose('infrastructure', 'stop', services=('keycloak',))


def stop_microservices():
    return compose('microservices', 'down')


def stop_microservice(microservice):
    if microservice == 'all':
        return stop_microservices()
    return compose(
        'microservices',
        'stop',
        services=(_policy_component.MICROSERVICE_COMPOSE_SERVICES[microservice],),
    )


def stop_frontends():
    return compose('frontends', 'down')


def stop_frontend(frontend):
    if frontend == 'all':
        return stop_frontends()
    return compose(
        'frontends',
        'stop',
        services=(_policy_component.FRONTEND_COMPOSE_SERVICES[frontend],),
    )


def stop_admin():
    return compose('admin', 'down')

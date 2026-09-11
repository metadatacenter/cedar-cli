"""Compatibility worker API; implementations live in docker_support."""
import json
import os
import re
import shlex
import socket
import ssl
import subprocess
import time
import urllib.request
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.util.BuildTrain import DockerTrain
from org.metadatacenter.util.DockerImages import DockerImages
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.CertificateWorker import CertificateError, CertificateWorker
from org.metadatacenter.worker.Worker import Worker

from org.metadatacenter.docker_support.acceptance import (
    _acceptance_errors,
    _backend_auth_error,
    _frontend_route_errors,
    _url_error,
)

from org.metadatacenter.docker_support.engine import (
    _compose_containers,
    _docker_command,
    _docker_server_version,
    _expected_compose_services,
    _port_owned_by_selected_compose_project,
    _published_ports,
    _stack_directory,
    running_compose_projects,
)

from org.metadatacenter.docker_support.environment import (
    _mode_label,
    _stack_names,
    mode_environment,
)

from org.metadatacenter.docker_support.images import (
    _inspect_image,
    _prepare_frontend_volumes,
    _prepare_microservice_volumes,
    _prepare_train_images,
    _prepare_writable_volumes,
    _train_image_names,
    build_images,
)

from org.metadatacenter.docker_support.lifecycle import (
    _individual_service_start,
    _individual_start,
    _port_has_listener,
    _print_failure_logs,
    _wait_for_acceptance,
    _wait_for_stacks,
    compose,
    preflight,
    start_admin,
    start_all,
    start_frontend,
    start_frontends,
    start_infrastructure,
    start_keycloak,
    start_microservice,
    start_microservices,
    stop_admin,
    stop_all,
    stop_frontend,
    stop_frontends,
    stop_infrastructure,
    stop_keycloak,
    stop_microservice,
    stop_microservices,
)

from org.metadatacenter.docker_support.output import (
    console,
)

from org.metadatacenter.docker_support.policy import (
    FRONTEND_COMPOSE_SERVICES,
    FRONTEND_LOG_VOLUMES,
    FRONTEND_NAMES,
    FRONTEND_PUBLIC_HOSTS,
    GIT_STATUS_CHAR_LIMIT,
    MICROSERVICE_COMPOSE_SERVICES,
    MICROSERVICE_WRITABLE_VOLUMES,
    STACKS,
    STATUS_SERVICE_ORDER,
)

from org.metadatacenter.docker_support.setup import (
    copy_certificates,
    create_certificates_volume,
    create_network,
    remove_containers,
    remove_images,
    remove_network,
    remove_volumes,
    validate,
)

from org.metadatacenter.docker_support.state import (
    _clear_active_deployment,
    _deployment_state_path,
    _record_active_deployment,
    active_deployment,
    active_train,
)

from org.metadatacenter.docker_support.status import (
    _container_image_status,
    _container_ports,
    _container_report,
    _container_snapshot,
    _health_text,
    _image_text,
    _ordered_status_services,
    _port_sort_key,
    _render_snapshot,
    _snapshot_ready,
    status,
)


from org.metadatacenter.docker_support import acceptance as _acceptance_component
from org.metadatacenter.docker_support import engine as _engine_component
from org.metadatacenter.docker_support import environment as _environment_component
from org.metadatacenter.docker_support import images as _images_component
from org.metadatacenter.docker_support import lifecycle as _lifecycle_component
from org.metadatacenter.docker_support import output as _output_component
from org.metadatacenter.docker_support import policy as _policy_component
from org.metadatacenter.docker_support import setup as _setup_component
from org.metadatacenter.docker_support import state as _state_component
from org.metadatacenter.docker_support import status as _status_component


class DockerWorker(Worker):

    @staticmethod
    def validate(environment=None):
        return _setup_component.validate(environment)

    @staticmethod
    def _docker_command(arguments, cwd=None, environment=None):
        return _engine_component._docker_command(arguments, cwd, environment)

    @staticmethod
    def _docker_server_version():
        return _engine_component._docker_server_version()

    @staticmethod
    def _train_image_names(stack, services=()):
        return _images_component._train_image_names(stack, services)

    @staticmethod
    def _inspect_image(reference):
        return _images_component._inspect_image(reference)

    @staticmethod
    def _prepare_train_images(train, stack_names, pull, environment, services_by_stack=None):
        return _images_component._prepare_train_images(train, stack_names, pull, environment, services_by_stack)

    @staticmethod
    def _prepare_microservice_volumes(reference):
        return _images_component._prepare_microservice_volumes(reference)

    @staticmethod
    def _prepare_frontend_volumes(reference):
        return _images_component._prepare_frontend_volumes(reference)

    @staticmethod
    def _prepare_writable_volumes(reference, volumes, owner):
        return _images_component._prepare_writable_volumes(reference, volumes, owner)

    @staticmethod
    def _expected_compose_services(stack_directory, environment=None):
        return _engine_component._expected_compose_services(stack_directory, environment)

    @staticmethod
    def _compose_containers(project_name):
        return _engine_component._compose_containers(project_name)

    @staticmethod
    def _container_report(container):
        return _status_component._container_report(container)

    @staticmethod
    def _container_ports(container):
        return _status_component._container_ports(container)

    @staticmethod
    def _port_sort_key(value):
        return _status_component._port_sort_key(value)

    @staticmethod
    def _container_image_status(stack_name, service, container, environment):
        return _status_component._container_image_status(stack_name, service, container, environment)

    @staticmethod
    def _stack_names(mode):
        return _environment_component._stack_names(mode)

    @staticmethod
    def _mode_label(mode):
        return _environment_component._mode_label(mode)

    @staticmethod
    def _deployment_state_path():
        return _state_component._deployment_state_path()

    @staticmethod
    def active_deployment():
        return _state_component.active_deployment()

    @staticmethod
    def running_compose_projects():
        return _engine_component.running_compose_projects()

    @staticmethod
    def _record_active_deployment(mode, train=None):
        return _state_component._record_active_deployment(mode, train)

    @staticmethod
    def _clear_active_deployment():
        return _state_component._clear_active_deployment()

    @staticmethod
    def active_train():
        return _state_component.active_train()

    @staticmethod
    def mode_environment(mode):
        return _environment_component.mode_environment(mode)

    @staticmethod
    def _container_snapshot(stack_names, environment=None):
        return _status_component._container_snapshot(stack_names, environment)

    @staticmethod
    def _ordered_status_services(stack_name, services):
        return _status_component._ordered_status_services(stack_name, services)

    @staticmethod
    def _snapshot_ready(snapshot):
        return _status_component._snapshot_ready(snapshot)

    @staticmethod
    def _render_snapshot(snapshot, mode):
        return _status_component._render_snapshot(snapshot, mode)

    @staticmethod
    def _health_text(detail):
        return _status_component._health_text(detail)

    @staticmethod
    def _image_text(value):
        return _status_component._image_text(value)

    @staticmethod
    def _backend_auth_error(timeout=10):
        return _acceptance_component._backend_auth_error(timeout)

    @staticmethod
    def _url_error(url, timeout=10):
        return _acceptance_component._url_error(url, timeout)

    @staticmethod
    def _frontend_route_errors(timeout=10):
        return _acceptance_component._frontend_route_errors(timeout)

    @staticmethod
    def _acceptance_errors(mode, timeout=10):
        return _acceptance_component._acceptance_errors(mode, timeout)

    @staticmethod
    def status(mode=DockerDeploymentMode.FULL):
        return _status_component.status(mode)

    @staticmethod
    def build_images(images, local=False, train=None):
        return _images_component.build_images(images, local, train)

    @staticmethod
    def create_network():
        return _setup_component.create_network()

    @staticmethod
    def create_certificates_volume():
        return _setup_component.create_certificates_volume()

    @staticmethod
    def copy_certificates():
        return _setup_component.copy_certificates()

    @staticmethod
    def remove_containers():
        return _setup_component.remove_containers()

    @staticmethod
    def remove_images():
        return _setup_component.remove_images()

    @staticmethod
    def remove_network():
        return _setup_component.remove_network()

    @staticmethod
    def remove_volumes():
        return _setup_component.remove_volumes()
    STACKS = STACKS

    @staticmethod
    def _stack_directory(stack):
        return _engine_component._stack_directory(stack)

    @staticmethod
    def _published_ports(stack_names, environment):
        return _engine_component._published_ports(stack_names, environment)

    @staticmethod
    def _port_has_listener(port):
        return _lifecycle_component._port_has_listener(port)

    @staticmethod
    def _port_owned_by_selected_compose_project(port, stack_names):
        return _engine_component._port_owned_by_selected_compose_project(port, stack_names)

    @staticmethod
    def preflight(mode, environment):
        return _lifecycle_component.preflight(mode, environment)

    @staticmethod
    def _print_failure_logs(snapshot, environment):
        return _lifecycle_component._print_failure_logs(snapshot, environment)

    @staticmethod
    def _wait_for_stacks(stack_names, deadline, mode, environment):
        return _lifecycle_component._wait_for_stacks(stack_names, deadline, mode, environment)

    @staticmethod
    def _wait_for_acceptance(mode, deadline):
        return _lifecycle_component._wait_for_acceptance(mode, deadline)

    @staticmethod
    def start_all(mode, pull='never', timeout=600, train=None):
        return _lifecycle_component.start_all(mode, pull, timeout, train)

    @staticmethod
    def stop_all(mode=None):
        return _lifecycle_component.stop_all(mode)

    @staticmethod
    def compose(stack, action, detach=False, pull=None, environment=None, services=()):
        return _lifecycle_component.compose(stack, action, detach, pull, environment, services)

    @staticmethod
    def _individual_start(stack, detach=False, pull='never', train=None):
        return _lifecycle_component._individual_start(stack, detach, pull, train)

    @staticmethod
    def _individual_service_start(stack, service, detach=False, pull='never', train=None):
        return _lifecycle_component._individual_service_start(stack, service, detach, pull, train)

    @staticmethod
    def start_infrastructure(detach=False, pull='never', train=None):
        return _lifecycle_component.start_infrastructure(detach, pull, train)

    @staticmethod
    def start_keycloak(detach=False, pull='never', train=None):
        return _lifecycle_component.start_keycloak(detach, pull, train)

    @staticmethod
    def start_microservices(detach=False, pull='never', train=None):
        return _lifecycle_component.start_microservices(detach, pull, train)

    @staticmethod
    def start_microservice(microservice, detach=False, pull='never', train=None):
        return _lifecycle_component.start_microservice(microservice, detach, pull, train)

    @staticmethod
    def start_frontends(detach=False, pull='never', train=None):
        return _lifecycle_component.start_frontends(detach, pull, train)

    @staticmethod
    def start_frontend(frontend, detach=False, pull='never', train=None):
        return _lifecycle_component.start_frontend(frontend, detach, pull, train)

    @staticmethod
    def start_admin(detach=False, pull='never', train=None):
        return _lifecycle_component.start_admin(detach, pull, train)

    @staticmethod
    def stop_infrastructure():
        return _lifecycle_component.stop_infrastructure()

    @staticmethod
    def stop_keycloak():
        return _lifecycle_component.stop_keycloak()

    @staticmethod
    def stop_microservices():
        return _lifecycle_component.stop_microservices()

    @staticmethod
    def stop_microservice(microservice):
        return _lifecycle_component.stop_microservice(microservice)

    @staticmethod
    def stop_frontends():
        return _lifecycle_component.stop_frontends()

    @staticmethod
    def stop_frontend(frontend):
        return _lifecycle_component.stop_frontend(frontend)

    @staticmethod
    def stop_admin():
        return _lifecycle_component.stop_admin()

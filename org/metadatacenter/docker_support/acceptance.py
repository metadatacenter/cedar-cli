"""CEDAR docker acceptance."""
from __future__ import annotations
import os
import ssl
import urllib.request
from org.metadatacenter.docker_support import engine as _engine_component
from org.metadatacenter.docker_support import policy as _policy_component


def _backend_auth_error(timeout=10):
    cedar_host = os.environ.get('CEDAR_HOST')
    if not cedar_host:
        return 'CEDAR_HOST is not defined; cannot check backend authentication routing'
    url = f'https://auth.{cedar_host}/realms/CEDAR/.well-known/openid-configuration'
    result = _engine_component._docker_command([
        'exec', 'server-resource', 'curl', '-kfsS', '--max-time', str(max(1, int(timeout))), url,
    ])
    if result.returncode == 0:
        return None
    return result.stderr.strip() or result.stdout.strip() or f'could not fetch {url} from server-resource'


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


def _frontend_route_errors(timeout=10):
    cedar_host = os.environ.get('CEDAR_HOST')
    if not cedar_host:
        return ['CEDAR_HOST is not defined; cannot check frontend routes']
    errors = []
    for host in _policy_component.FRONTEND_PUBLIC_HOSTS:
        url = f'https://{host}.{cedar_host}/'
        error = _url_error(url, timeout=timeout)
        if error:
            errors.append(error)
    return errors


def _acceptance_errors(mode, timeout=10):
    errors = []
    auth_error = _backend_auth_error(timeout=timeout)
    if auth_error:
        errors.append(f'backend authentication route: {auth_error}')
    if mode.checks_frontend_routes:
        errors.extend(_frontend_route_errors(timeout=timeout))
    return errors

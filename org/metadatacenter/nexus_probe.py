"""Opt-in write probe restricted to a dedicated disposable Nexus raw repository."""
import base64
import hashlib
import urllib.error
import urllib.request
import uuid
from org.metadatacenter.util.NexusCredentials import environment_with_nexus_credentials

PROBE_BASE = 'https://nexus.bmir.stanford.edu/repository/cedar-cli-probes/'


def upload_probe(*, environment=None, opener=None):
    environment = environment_with_nexus_credentials(environment)
    username, password = environment.get('BMIR_NEXUS_USERNAME'), environment.get('BMIR_NEXUS_PASSWORD')
    if not username or not password:
        raise ValueError('Nexus credentials are required for the disposable write probe')
    opener = opener or urllib.request.urlopen
    identifier = uuid.uuid4().hex
    url = PROBE_BASE + identifier + '/probe.bin'
    content = (identifier.encode() * 2048)  # 64 KiB; deliberately not a release artifact.
    auth = base64.b64encode(f'{username}:{password}'.encode()).decode()
    failure = None
    try:
        with opener(urllib.request.Request(url, data=content, method='PUT',
                headers={'Authorization':'Basic ' + auth, 'Content-Type':'application/octet-stream'}), timeout=30) as response:
            if response.status not in (200,201,204):
                raise ValueError(f'PUT returned HTTP {response.status}')
        with opener(urllib.request.Request(url, headers={'Authorization':'Basic ' + auth}), timeout=30) as response:
            if response.read() != content:
                raise ValueError('GET returned different probe bytes')
    except (OSError, ValueError) as error:
        failure = f'Disposable Nexus upload/read probe failed at {url}: {error}'
    finally:
        # A lost PUT response can still have stored the object. Always attempt removal
        # of this unique path, even when the write outcome is unknown.
        try:
            with opener(urllib.request.Request(url, method='DELETE',
                    headers={'Authorization':'Basic ' + auth}), timeout=30) as response:
                if response.status not in (200,204):
                    raise ValueError(f'DELETE returned HTTP {response.status}')
        except urllib.error.HTTPError as error:
            if error.code != 404:
                failure = (failure + '; ' if failure else '') + f'probe cleanup failed at {url}: HTTP {error.code}'
        except (OSError, ValueError) as error:
            failure = (failure + '; ' if failure else '') + f'probe cleanup failed at {url}: {error}'
    if failure:
        raise ValueError(failure)
    return {'url':url, 'bytes':len(content), 'sha256':hashlib.sha256(content).hexdigest(), 'deleted':True}

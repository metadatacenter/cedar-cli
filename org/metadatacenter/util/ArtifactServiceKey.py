"""Manage artifact's installation-local service credential without displaying it."""
import fcntl
import os
import re
import secrets
import tempfile

from org.metadatacenter.util.ModeManager import ModeError, ModeManager

CURRENT = 'CEDAR_ARTIFACT_SERVICE_API_KEY'
PREVIOUS = 'CEDAR_ARTIFACT_SERVICE_PREVIOUS_API_KEY'
KEY = re.compile(r'[A-Za-z0-9_-]{43}')


def manage_artifact_key(action):
    directory = ModeManager.cedar_home() / '.cedar' / 'secrets'
    if directory.is_symlink():
        raise ModeError('The secret directory must not be a symbolic link')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / 'artifact-service.sh'
    lock_fd = os.open(directory / 'artifact-service.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink():
            raise ModeError('The artifact service key file must not be a symbolic link')
        values = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if not line or line.startswith('#'):
                    continue
                match = re.fullmatch(r'export (CEDAR_ARTIFACT_SERVICE_(?:PREVIOUS_)?API_KEY)="([A-Za-z0-9_-]*)"', line)
                if not match or (match[2] and not KEY.fullmatch(match[2])) or match[1] in values:
                    raise ModeError('The artifact service key file has an invalid format; no changes made')
                values[match[1]] = match[2]
            if not KEY.fullmatch(values.get(CURRENT, '')):
                raise ModeError('The artifact service key file has no valid current key; no changes made')
        if action == 'init':
            if values:
                return 'Artifact service key already exists; unchanged.'
            values = {CURRENT: secrets.token_urlsafe(32), PREVIOUS: ''}
        elif action == 'rotate':
            if not values:
                raise ModeError('Initialize the artifact service key before rotating it')
            if values.get(PREVIOUS):
                raise ModeError('A previous key is still retained; finish that rollout and retire it before rotating again')
            values = {CURRENT: secrets.token_urlsafe(32), PREVIOUS: values[CURRENT]}
        elif action == 'retire':
            if not values:
                raise ModeError('No artifact service key exists')
            values[PREVIOUS] = ''
        else:
            raise ModeError('Choose init, rotate, or retire')
        fd, temporary = tempfile.mkstemp(prefix='.artifact-service-', dir=directory)
        try:
            with os.fdopen(fd, 'w') as output:
                output.write('# Managed by cedarcli env artifact-key. Keep private.\n')
                for name in (CURRENT, PREVIOUS):
                    output.write(f'export {name}="{values[name]}"\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        if action == 'retire':
            return 'Previous artifact service key retired. Restart artifact to stop accepting it.'
        if action == 'init':
            return ('Artifact service key saved privately. For first deployment, upgrade bridge, then resource and worker '
                    'with this key, then artifact to enforce it. Distribute the same key securely to all backend hosts.')
        return ('Artifact service key saved privately. Restart artifact first to accept both keys, then resource and worker. '
                'After a rotation, verify those callers before running cedarcli env artifact-key retire.')

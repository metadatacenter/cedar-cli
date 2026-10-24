"""Local task outputs and verification evidence. Also called by JavaScript gates.

Only declared tasks participate. Inputs are content addressed; outputs are restored into an
isolated build, never into the working checkout. Missing/corrupt entries simply run again.
"""
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import time
import uuid

SCHEMA = 1


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def tree(path):
    """Hash names, modes, links and contents; an absent input has its own identity."""
    path = Path(path)
    if path.is_symlink():
        return {'link': os.readlink(path), 'target': digest(path.read_bytes()) if path.is_file() else None}
    if path.is_file():
        return {'mode': path.stat().st_mode & 0o777, 'sha256': digest(path.read_bytes())}
    if path.is_dir():
        return {p.name: tree(p) for p in sorted(path.iterdir())}
    return None


def safe_path(root, name):
    path = Path(name)
    if path.is_absolute() or not path.parts or '..' in path.parts or name == '.':
        raise ValueError('Invalid task output path')
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Task output escapes its workspace')
    return target


def identity(config, name, environment=None):
    spec = config['tasks'].get(name)
    if spec is None:
        return None
    if spec.get('equivalentCommand'):
        # CEE packages before releasing its consumers and verifies that same package after
        # browser checks. Reuse the first execution only for the identical script/environment.
        if environment != {'script': spec['script'], 'environment': {}}:
            return None
        name, environment = spec['equivalentCommand'], {}
    root = Path(config['root'])
    locks = {}
    for relative in config['locks']:
        path = root / relative
        if not path.is_file():
            return None
        # npm records scratch-relative file URLs. The immutable artifact digest is the input.
        value = re.sub(r'file:[^"\n]*?([a-f0-9]{64}\.tgz)', r'file:reactor/\1', path.read_text())
        locks[relative] = digest(value.encode())
    reads = {relative: tree(safe_path(root, relative)) for relative in spec.get('reads', [])}
    if any(value is None for value in reads.values()):
        return None
    inputs = {'schema': SCHEMA, 'task': name, 'base': config['base'],
              'source': spec['source'], 'locks': locks, 'reads': reads,
              'environment': environment or {}, 'outputs': spec.get('outputs', [])}
    key = digest(encoded(inputs))
    return key, inputs, Path(config['directory']) / key


def reuse(config, name, environment=None):
    entry = identity(config, name, environment)
    if entry is None:
        return None
    key, inputs, directory = entry
    pending = Path(config.get('writeDirectory', config['directory'])) / key
    if pending.is_dir():
        directory = pending
    try:
        record = json.loads((directory / 'result.json').read_text())
        if (not isinstance(record, dict) or record.get('schema') != SCHEMA or record.get('key') != key
                or record.get('inputs') != inputs or record.get('exitCode') != 0
                or not isinstance(record.get('passedAt'), str)):
            return None
        datetime.fromisoformat(record['passedAt'].replace('Z', '+00:00'))
        outputs = config['tasks'][name].get('outputs', [])
        root = Path(config['root'])
        if outputs:
            archive = directory / 'outputs.tar'
            if digest(archive.read_bytes()) != record.get('archiveSha256'):
                return None
            with tempfile.TemporaryDirectory(prefix='restore-', dir=root) as temporary:
                stage = Path(temporary)
                with tarfile.open(archive) as bundle:
                    bundle.extractall(stage, filter='data')
                if {p: tree(safe_path(stage, p)) for p in outputs} != record.get('outputs'):
                    return None
                for relative in outputs:
                    target = safe_path(root, relative)
                    restored = safe_path(stage, relative)
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists() or target.is_symlink():
                        target.unlink()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(restored), target)
        return record
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        return None


def save(config, name, environment=None, log=''):
    # A Playwright success after retries is useful evidence, but must not become a permanent hit.
    clean_log = re.sub(r'\x1b\[[0-9;]*m', '', log)
    if re.search(r'\b[1-9]\d* flaky\b', clean_log):
        Path(config['uncacheable']).touch()
        return None
    entry = identity(config, name, environment)
    if entry is None:
        return None
    key, inputs, directory = entry
    directory = Path(config.get('writeDirectory', config['directory'])) / key
    root = Path(config['root'])
    outputs = config['tasks'][name].get('outputs', [])
    state = {p: tree(safe_path(root, p)) for p in outputs}
    if any(value is None for value in state.values()):
        return None
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='task-', dir=directory.parent) as temporary:
        stage = Path(temporary)
        record = {'schema': SCHEMA, 'key': key, 'inputs': inputs, 'exitCode': 0,
                  'passedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'outputs': state}
        if outputs:
            archive = stage / 'outputs.tar'
            with tarfile.open(archive, 'w') as bundle:
                for relative in outputs:
                    bundle.add(safe_path(root, relative), arcname=relative)
            record['archiveSha256'] = digest(archive.read_bytes())
        (stage / 'output.log').write_text(log)
        (stage / 'result.json').write_bytes(encoded(record))
        promote(stage, directory)
    return record


def promote(stage, directory):
    # Readers racing the brief gap get a miss, never a partially written entry.
    retired = directory.with_name('retired-' + uuid.uuid4().hex)
    try:
        try:
            os.rename(directory, retired)
        except FileNotFoundError:
            pass
        try:
            os.rename(stage, directory)
        except OSError:
            if not directory.is_dir():
                raise
    finally:
        shutil.rmtree(retired, ignore_errors=True)


def publish(config):
    """Publish completed stages only after the CLI has checked the original source guard."""
    pending = Path(config['writeDirectory'])
    if not pending.is_dir():
        return
    destination = Path(config['directory'])
    destination.mkdir(parents=True, exist_ok=True)
    for stage in pending.iterdir():
        if re.fullmatch(r'[a-f0-9]{64}', stage.name) and (stage / 'result.json').is_file():
            promote(stage, destination / stage.name)


def main():
    operation, path, name, environment = sys.argv[1:5]
    config = json.loads(Path(path).read_text())
    if operation == 'reuse':
        result = reuse(config, name, json.loads(environment))
    elif operation == 'save':
        result = save(config, name, json.loads(environment), sys.stdin.read())
    else:
        raise ValueError('Unknown cache operation')
    print(json.dumps({'key': result['key'], 'passedAt': result['passedAt']} if result else None))


if __name__ == '__main__':
    main()

"""Persistent evidence for the disposable frontend build, independent of npm version labels."""
from contextvars import ContextVar
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from org.metadatacenter.reactor import ReactorError, store_root

_build = ContextVar('frontend_build_evidence', default=None)
_inputs = ContextVar('frontend_build_inputs', default=None)


class Selection(dict):
    """Package selection with its immutable build evidence reference."""
    def __init__(self, packages, evidence):
        super().__init__(packages)
        self.evidence = evidence


def source_identity(source):
    """Include tracked edits and nonignored new files, not generated dependencies/caches."""
    source = Path(source)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args])
    paths = sorted(set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0')) - {b''})
    digest = hashlib.sha256()
    for raw in paths:
        path = source / os.fsdecode(raw)
        digest.update(raw + b'\0')
        if path.is_symlink():
            digest.update(b'link\0' + os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(str(path.stat().st_mode & 0o777).encode() + b'\0')
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        else:
            digest.update(b'deleted')
    return {'revision': git('rev-parse', 'HEAD').decode().strip(),
            'sourceSha256': digest.hexdigest(),
            'dirty': bool(git('status', '--porcelain', '--untracked-files=normal').strip())}


@contextlib.contextmanager
def session(plan=None):
    from org.metadatacenter.util.Util import Util
    inputs = {}
    def visit(task):
        if getattr(task, 'parameters', {}).get('isolated_frontend_build'):
            source = str(Path(Util.get_wd(task.repo)).resolve())
            inputs[source] = source_identity(source)
        for child in task.tasks:
            visit(child)
    if plan is not None:
        visit(plan)
    input_token = _inputs.set(inputs)
    token = _build.set({})
    try:
        yield
    finally:
        _build.reset(token)
        _inputs.reset(input_token)


def begin(source):
    if _build.get() is None:
        return None
    identity = source_identity(source)
    original = (_inputs.get() or {}).get(str(Path(source).resolve()), identity)
    if identity != original:
        raise ReactorError(f'Frontend source changed before its reactor task: {source}')
    return identity


def record(source, copy, identity, commands, checks=()):
    if identity is None:
        return
    if source_identity(source) != identity:
        raise ReactorError(f'Frontend source changed while building {source}')
    manifests = {}
    edges = []
    # Prune node_modules before descending; a recursive glob traverses entire installs.
    for directory, children, files in os.walk(copy):
        children[:] = [name for name in children if name not in
                       {'node_modules', '.git', '.angular', 'dist', 'dist-npm', 'dist-bundle'}]
        for name in ('package.json', 'package-lock.json'):
            if name not in files:
                continue
            path = Path(directory) / name
            relative = path.relative_to(copy).as_posix()
            value = json.loads(path.read_text())
            manifests[relative] = value
            if name == 'package.json':
                for section in ('dependencies', 'devDependencies', 'optionalDependencies'):
                    for dependency, spec in value.get(section, {}).items():
                        if isinstance(spec, str) and spec.startswith('file:') and spec.endswith('.tgz'):
                            edges.append({'manifest': relative, 'section': section,
                                          'dependency': dependency, 'sha256': Path(spec[5:]).stem})
    _build.get()[str(Path(source).resolve())] = {
        'source': identity, 'commands': list(commands), 'dependencies': edges,
        'testDependencies': list(checks),
        'resolvedManifests': manifests,
    }


def finish(home, packages):
    records = _build.get()
    if records is None:
        return None
    # Check all inputs again: a producer edited during a later consumer invalidates the run.
    for source, record in records.items():
        if source_identity(source) != record['source']:
            raise ReactorError(f'Frontend source changed during reactor: {source}')
    value = {'schemaVersion': 1, 'packages': packages,
             'repositories': {str(Path(source).relative_to(Path(home).resolve())): record
                              for source, record in records.items()}}
    content = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    digest = hashlib.sha256(content).hexdigest()
    directory = store_root(home) / 'builds'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (digest + '.json')
    # Content-addressed records are immutable, including when invocations overlap.
    with tempfile.TemporaryDirectory(prefix='record-', dir=directory) as temporary:
        complete = Path(temporary) / 'record.json'
        complete.write_bytes(content)
        try:
            os.link(complete, destination)
        except FileExistsError:
            if destination.read_bytes() != content:
                raise ReactorError(f'Corrupt reactor evidence: {destination}')
    return digest

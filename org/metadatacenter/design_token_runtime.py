"""Read-only comparison of token consumers with the selected reactor build.

This checks source and artifact identity, not process health or HTTP-served bytes.
The reactor's deployment verification remains the latter's authority.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

from org.metadatacenter.reactor_evidence import source_identity

TOKEN_PACKAGE = '@org.metadatacenter/cedar-design-tokens'


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Invalid reactor content digest')
    return value


def inspect_runtime(home, repos=None):
    home = Path(home).resolve()
    store = home / '.reactor'
    result = {'state': 'missing', 'build': None, 'tokenArtifact': None, 'repositories': [], 'errors': []}
    if not (store / 'runtime.json').is_file():
        result['errors'].append('No selected reactor runtime; run cedarcli build frontends')
        return result
    try:
        selected = json.loads((store / 'runtime.json').read_text())
        build = _digest(selected['build'])
        result['build'] = build
        content = (store / 'builds' / (build + '.json')).read_bytes()
        if hashlib.sha256(content).hexdigest() != build:
            raise ValueError('Selected reactor build record does not match its digest')
        record = json.loads(content)
        if record.get('schemaVersion') != 1 or record['packages'] != selected['packages']:
            raise ValueError('Runtime package selection differs from its build record')
        packages = selected['packages']
        result['tokenArtifact'] = _digest(packages['cedar-design-tokens'])
        for name, digest in packages.items():
            digest = _digest(digest)
            artifact = store / 'artifacts' / (digest + '.tgz')
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                raise ValueError(f'Reactor artifact does not match its digest: {name}')
        records = record['repositories']
        if 'cedar-design-tokens' not in records:
            raise ValueError('Selected build has no token source evidence')
        selected_repos = set(repos or [])
        if not repos:
            # Discover current consumers too: a newly added host cannot disappear from the
            # report merely because the older build did not know about it.
            for manifest in [*home.glob('cedar-*/package.json'), *home.glob('cedar-*/*-src/package.json')]:
                package = json.loads(manifest.read_text())
                if any(TOKEN_PACKAGE in package.get(section, {}) for section in
                       ('dependencies', 'devDependencies', 'optionalDependencies')):
                    selected_repos.add(str(manifest.parent.relative_to(home)))
        seen = set()
        for name, entry in sorted(records.items()):
            path = Path(name)
            if path.is_absolute() or '..' in path.parts or not (home / path).resolve().is_relative_to(home):
                raise ValueError('Invalid source path in reactor build')
            reads_tokens = name == 'cedar-design-tokens' or any(
                edge['dependency'] == TOKEN_PACKAGE for edge in entry['dependencies'])
            if not reads_tokens or (repos and name != 'cedar-design-tokens'
                                    and path.parts[0] not in selected_repos and name not in selected_repos):
                continue
            seen.update((name, path.parts[0]))
            for edge in entry['dependencies']:
                package = edge['dependency'].rsplit('/', 1)[-1]
                if packages.get(package) != edge['sha256']:
                    raise ValueError(f'{name}: dependency differs from selected artifact: {package}')
            built = entry['source']
            current = source_identity(home / path)
            result['repositories'].append({
                'repo': name, 'state': 'current' if current['sourceSha256'] == _digest(built['sourceSha256']) else 'stale',
                'builtRevision': built['revision'], 'currentRevision': current['revision'],
                'builtSourceSha256': built['sourceSha256'], 'currentSourceSha256': current['sourceSha256'],
                'dirty': current['dirty'],
            })
        for name in selected_repos - seen:
            result['errors'].append(f'{name}: no selected reactor token-consumer evidence')
        result['state'] = ('invalid' if result['errors'] else 'stale'
                           if any(row['state'] == 'stale' for row in result['repositories']) else 'current')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as error:
        result['state'] = 'invalid'
        result['errors'].append(str(error))
    return result


def print_runtime(report):
    print(f"Selected reactor token runtime: {report['state']}")
    if report['build']:
        print(f"  build: {report['build']}")
        print(f"  token artifact: {report['tokenArtifact']}")
    for row in report['repositories']:
        print(f"  {row['repo']}: {row['state']} "
              f"(built {row['builtRevision'][:12]} / source {row['builtSourceSha256'][:12]}, "
              f"current {row['currentRevision'][:12]} / source {row['currentSourceSha256'][:12]})")
    for error in report['errors']:
        print(f'  {error}')
    if report['state'] != 'current':
        print('  Run cedarcli build frontends to build and deploy current working-tree sources.')
    print('  Source/artifact comparison only; served-byte verification and smoke run during the frontend build.')

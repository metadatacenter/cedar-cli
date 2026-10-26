"""Declared task boundaries for the local frontend reactor.

Unknown source files remain inputs. Narrowing excludes only established, independent test
surfaces; shared fixtures, configuration and build scripts remain inputs to every task.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from org.metadatacenter import reactor, reactor_evidence, task_cache

CONFIG = 'CEDAR_TASK_CACHE_CONFIG'


def definitions(repo):
    def task(kind, outputs=(), reads=()):
        return {'kind': kind, 'outputs': list(outputs), 'reads': list(reads)}
    name = repo.name
    if name == 'cedar-embeddable-editor':
        bundles = [f'visual/public/{p}' for p in ['cedar-embeddable-editor.js',
                   'cedar-embeddable-editor.host-fonts.js', 'bundle-manifest.json',
                   'bundle-manifest.host-fonts.json']]
        return {
            'npm run build:production': task('build', ['dist']),
            # Rejoining is cheap and refreshes the mtime contract checked by the visual gate.
            'npm run package:npm:prebuilt': task('package', ['dist-types', 'dist-npm'], ['dist', *bundles]),
            'domain': task('domain'), 'unit': task('unit'), 'coordinator': task('unit', reads=['dist']),
            'types': task('all'), 'lint': task('all'),
            'visual': task('browser', reads=bundles),
            'package': {**task('package', ['dist-types', 'dist-npm'], ['dist', *bundles]),
                        'equivalentCommand': 'npm run package:npm:prebuilt',
                        'script': 'package:npm:prebuilt'},
        }
    if name == 'cedar-embeddable-designer':
        return {
            'dist': task('package', ['dist', 'dist-bundle', 'dist-types', 'dist-npm']),
            'unit': task('unit'), 'lint': task('all'), 'types': task('all'),
            'boundaries': task('all'), 'packaging': task('all'), 'conformance': task('all'),
            'browser': task('browser', reads=['dist-bundle/cedar-embeddable-designer.js']),
            'visual': task('browser', reads=['dist-bundle/cedar-embeddable-designer.js']),
        }
    if name == 'cedar-model-typescript-library':
        return {
            'npm run build': task('package', ['dist', 'package-dist.json']),
            'coverage': task('unit', ['coverage']), 'lint': task('all'), 'types': task('all'),
            'yaml': task('all'), 'json': task('all'),
            'package': task('package', reads=['dist']),
            'browser': task('browser', reads=['dist']),
        }
    if name == 'cedar-workspace':
        return {'npm test': task('unit'),
                'npm run build': task('build', ['.workspace-build', 'app/workspace-build']),
                'npm run test:visual': task('browser', reads=['.workspace-build'])}
    if name in {'cedar-monitoring-src', 'cedar-bridging-src', 'cedar-openview-src'}:
        return {'npm test -- --watch=false': task('unit'),
                'npm run build': task('build', ['dist']),
                'npm --prefix ../browser test': task('browser', reads=['dist'])}
    # Other repositories retain whole-repository result reuse until their task inputs/outputs
    # are explicitly declared. In particular, a combined test/build command is never skipped
    # without retaining everything its subsequent commands require.
    return {}


def includes(path, kind, repository):
    parts = Path(path).parts
    if any(p in {'.github', 'docs'} for p in parts) or Path(path).name in {'AGENTS.md', 'CLAUDE.md', 'GEMINI.md'}:
        return False
    # READMEs can be shipped and executed as package examples, so packaging keeps them.
    if kind not in {'package', 'all'} and Path(path).name in {'README.md', 'CHANGELOG.md'}:
        return False
    browser_test = any(p in {'browser', 'visual'} for p in parts) and (
        'tests' in parts or any(p.endswith('-snapshots') for p in parts))
    if kind in {'unit', 'domain', 'build', 'package'} and browser_test:
        return False
    source_spec = path.startswith('src/') and bool(re.search(r'\.(spec|test)\.[cm]?[jt]sx?$', path))
    if kind == 'browser' and source_spec:
        return False
    if kind == 'build' and repository in {'cedar-workspace', 'cedar-embeddable-editor'} and source_spec:
        return False
    # CEE's domain harness and browser fixtures can be imported by unit tests. Keep them;
    # excluding an entire harness directory would incorrectly hide those dependencies.
    return True


def configure(environment, repo, result, home, source, build_root):
    environment.pop(CONFIG, None)
    tasks = definitions(repo)
    if result is None or not tasks:
        return None
    source, root = Path(source), Path(build_root)
    checkout = Path(subprocess.check_output(['git', '-C', str(source), 'rev-parse', '--show-toplevel'], text=True).strip())
    files = {}
    for raw in reactor_evidence.source_paths(checkout):
        name = os.fsdecode(raw)
        relative = os.path.relpath(checkout / name, source)
        files[relative] = task_cache.tree(root / relative)
    for name, spec in tasks.items():
        groups = {}
        for path, value in files.items():
            if includes(path, spec['kind'], repo.name):
                groups.setdefault(path.split('/')[0], {})[path] = value
        spec['source'] = {group: task_cache.digest(task_cache.encoded(values)) for group, values in groups.items()}
    base = {k: v for k, v in result[1].items() if k not in {'source', 'commands', 'packageReadyAfter'}}
    locks = [path for path in files if Path(path).name == 'package-lock.json']
    config = {'root': str(root), 'directory': str(reactor.store_root(home) / 'task-results'),
              'writeDirectory': str(root / '.cedar-task-cache-pending'),
              'base': base, 'tasks': tasks, 'locks': locks,
              'uncacheable': str(root / '.cedar-cache-unstable'),
              'python': sys.executable, 'engine': str(Path(task_cache.__file__).resolve())}
    path = root / '.cedar-task-cache.json'
    path.write_text(json.dumps(config))
    environment[CONFIG] = str(path)
    environment['CEDAR_STAGE_CACHE_MODULE'] = (
        Path(__file__).resolve().parents[2] / 'scripts/javascript/reactor-stage-cache.mjs').as_uri()
    return config


def load(environment):
    path = (environment or {}).get(CONFIG)
    return json.loads(Path(path).read_text()) if path else None

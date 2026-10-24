"""Resume completed, output-free verification stages after a later stage fails.

Use the entire repository fingerprint: a source, dependency, command, oracle or tooling change
invalidates every stage. Never cache producers of dist, packages or generated fixtures. This is
deliberately conservative; stages do not yet have independently declared source dependencies.
"""
import json
from pathlib import Path

from org.metadatacenter import reactor

STAGES = {
    'cedar-embeddable-editor': ['domain', 'unit', 'lint', 'types', 'coordinator', 'visual'],
    'cedar-embeddable-designer': ['unit', 'lint', 'types', 'boundaries', 'packaging',
                                  'conformance', 'browser', 'visual'],
    'cedar-model-typescript-library': ['coverage', 'lint', 'types', 'yaml', 'json', 'browser'],
}


def configure(environment, repo, result, home):
    # Do not inherit another invocation's cache, including when --no-reuse is selected.
    environment.pop('CEDAR_STAGE_CACHE_MODULE', None)
    environment.pop('CEDAR_STAGE_CACHE', None)
    if result is None or repo.name not in STAGES:
        return
    environment['CEDAR_STAGE_CACHE_MODULE'] = (
        Path(__file__).resolve().parents[2] / 'scripts/javascript/reactor-stage-cache.mjs').as_uri()
    environment['CEDAR_STAGE_CACHE'] = json.dumps({
        'directory': str(reactor.store_root(home) / 'stage-results'),
        'fingerprint': result[0], 'stages': STAGES[repo.name],
    })

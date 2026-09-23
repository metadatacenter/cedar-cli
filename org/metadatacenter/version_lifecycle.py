"""Inventory artifact version chains; repair only unambiguous derived flags."""
import json
import re
import requests
from org.metadatacenter.util.InvocationContext import invocation_environment

FLAGS = ('isLatestVersion', 'isLatestDraftVersion', 'isLatestPublishedVersion')
SCHEMA_TYPES = ('template', 'element', 'field')


def analyze(rows):
    nodes = {row['node']['@id']: row['node'] for row in rows}
    links = {row['node']['@id']: row['previous'] for row in rows}
    findings, expected = [], {}
    children = {key: [] for key in nodes}
    versions = {}
    for key, node in nodes.items():
        raw = node.get('pav:version', '')
        if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', raw):
            findings.append({'id': key, 'issue': 'invalid-version'})
        else:
            version = tuple(map(int, raw.split('.')))
            if not any(version) or max(version) > 2147483647:
                findings.append({'id': key, 'issue': 'invalid-version'})
            else:
                versions[key] = version
        if node.get('bibo:status') not in ('bibo:draft', 'bibo:published'):
            findings.append({'id': key, 'issue': 'invalid-status'})
        previous = node.get('pav:previousVersion')
        if previous:
            if previous not in nodes:
                findings.append({'id': key, 'issue': 'missing-predecessor', 'previous': previous})
            else:
                children[previous].append(key)
                if node['resourceType'] != nodes[previous]['resourceType']:
                    findings.append({'id': key, 'issue': 'mixed-artifact-types'})
        if sorted(links[key]) != ([previous] if previous else []):
            findings.append({'id': key, 'issue': 'property-relationship-disagreement'})
    for key, node in nodes.items():
        previous = node.get('pav:previousVersion')
        if previous in versions and key in versions and versions[key] <= versions[previous]:
            findings.append({'id': key, 'issue': 'non-increasing-version'})
        if len(children[key]) > 1:
            findings.append({'id': key, 'issue': 'branched-history'})
        if children[key] and node.get('bibo:status') == 'bibo:draft':
            findings.append({'id': key, 'issue': 'draft-has-successor'})
    seen = set()
    for key in nodes:
        if key in seen:
            continue
        group, todo = set(), [key]
        while todo:
            current = todo.pop()
            if current in group:
                continue
            group.add(current)
            previous = nodes[current].get('pav:previousVersion')
            if previous in nodes:
                todo.append(previous)
            todo.extend(children[current])
        seen.update(group)
        drafts = [key for key in group if nodes[key].get('bibo:status') == 'bibo:draft']
        if len(drafts) > 1:
            findings.append({'id': key, 'issue': 'multiple-drafts'})
        if any(f['id'] in group for f in findings):
            continue  # Ambiguous history is reported, never guessed during an apply.
        published = [key for key in group if nodes[key].get('bibo:status') == 'bibo:published']
        release = max(published, key=lambda k: versions[k]) if published else None
        draft = drafts[0] if drafts else None
        latest = draft or release
        for current in group:
            target = dict(zip(FLAGS, (current == latest, current == draft, current == release)))
            expected[current] = target
            if any(nodes[current].get(flag) is not value for flag, value in target.items()):
                findings.append({'id': current, 'issue': 'incorrect-latest-flags', 'expected': target})
    return findings, expected


def check_versioning(apply=False):
    env = invocation_environment()
    host = env['CEDAR_NEO4J_HOST']
    port = env.get('CEDAR_NEO4J_REST_PORT', '7474')
    base = f'http://{host}:{port}/db/neo4j/tx'
    session = requests.Session()
    session.auth = (env['CEDAR_NEO4J_USER_NAME'], env['CEDAR_NEO4J_USER_PASSWORD'])
    transaction = None

    def run(url, statement, parameters=None):
        response = session.post(url, json={'statements': [{'statement': statement, 'parameters': parameters or {}}]}, timeout=60)
        response.raise_for_status()
        result = response.json()
        if result.get('errors'):
            # Return database error codes; do not expose connection or query credentials.
            raise RuntimeError(', '.join(e['code'] for e in result['errors']))
        return result

    try:
        if apply:
            opened = run(base, "MATCH (n:CedarVersionLock {id:'lifecycle'}) SET n.revision=coalesce(n.revision,0)+1 RETURN n.id")
            transaction = opened['commit'].removesuffix('/commit')
            if not opened['results'][0]['data']:
                raise RuntimeError('Deploy the lifecycle implementation before applying flag repairs')
        result = run(transaction or base + '/commit',
                     'MATCH (a:Artifact) WHERE a.resourceType IN $types '
                     'OPTIONAL MATCH (a)-[:PREVIOUSVERSION]->(p) '
                     'RETURN properties(a), collect(p._id)', {'types': list(SCHEMA_TYPES)})
        rows = [{'node': {('@id' if k == '_id' else k.replace('_', ':')): v for k, v in item['row'][0].items()}, 'previous': item['row'][1]} for item in result['results'][0]['data']]
        findings, expected = analyze(rows)
        repair_ids = [f['id'] for f in findings if f['issue'] == 'incorrect-latest-flags']
        if apply:
            updates = [{'id': key, **expected[key]} for key in repair_ids]
            run(transaction + '/commit',
                'UNWIND $updates AS u MATCH (a:Artifact {_id:u.id}) '
                'SET a.isLatestVersion=u.isLatestVersion, a.isLatestDraftVersion=u.isLatestDraftVersion, '
                'a.isLatestPublishedVersion=u.isLatestPublishedVersion '
                'MERGE (j:CedarVersionProjection {resourceId:u.id}) '
                'SET j.syncPrevious=coalesce(j.syncPrevious,false), j.updatedAt=timestamp()', {'updates': updates})
            transaction = None
        queues = run(transaction or base + '/commit',
                     'OPTIONAL MATCH (p:CedarVersionProjection) WITH count(p) AS projections '
                     'OPTIONAL MATCH (d:CedarArtifactDeletionOutbox) '
                     'RETURN projections, count(CASE WHEN coalesce(d.parked,false)=false THEN d END), '
                     'count(CASE WHEN d.parked=true THEN d END)')['results'][0]['data'][0]['row']
        remaining = [f for f in findings if not (apply and f['issue'] == 'incorrect-latest-flags')]
        # Report what was audited alongside the verdict. `artifacts` is the whole sample, and it
        # covers schema artifacts only -- no instances -- so a small number with no findings is a
        # narrow check passing, not a broad one. Read as a clean bill of health on a graph holding
        # far more than this, it would be the most expensive kind of wrong answer: the reassuring
        # one. An operator cannot tell the two apart unless the scope is stated.
        print(json.dumps({'scope': {'resourceTypes': list(SCHEMA_TYPES), 'auditedArtifacts': len(rows),
                                    'covers': 'schema artifacts only; metadata instances are not audited'},
                          'artifacts': len(rows), 'pendingProjections': queues[0],
                          'pendingDeletions': queues[1], 'parkedDeletions': queues[2], 'repaired': len(repair_ids) if apply else 0,
                          'findings': remaining}, indent=2))
        return 1 if remaining or any(queues) else 0
    except (requests.RequestException, RuntimeError, KeyError, ValueError) as error:
        print(f'Artifact versioning check could not complete ({type(error).__name__}). Check the selected profile and Neo4j availability.')
        return 1
    finally:
        if transaction:
            try:
                session.delete(transaction, timeout=15)
            except requests.RequestException:
                pass  # An uncommitted Neo4j transaction expires without applying its repairs.
        session.close()

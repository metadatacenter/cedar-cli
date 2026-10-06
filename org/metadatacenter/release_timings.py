"""Measured coordinator-stage time, with CI sleeps and retry backoff separated."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import time

from org.metadatacenter.release_support.errors import ReleaseError

_current_waits = ContextVar('release_stage_waits', default=None)


def timed_wait(kind, sleeper, seconds):
    started = time.monotonic()
    try:
        return sleeper(seconds)
    finally:
        waits = _current_waits.get()
        if waits is not None:
            waits[kind] = waits.get(kind, 0) + max(0, time.monotonic() - started)


def workload_signature(manifest):
    # Versions/source naturally change across releases. Compare the configured
    # workload and concurrency, not those identities, and retain both in the ledger.
    fields = ('releaseRepositories', 'mavenRepositories', 'mavenPhases', 'buildConcurrency',
              'developmentVerificationPolicy')
    plan = {key: manifest.get(key) for key in fields}
    plan['npmSurfaces'] = manifest.get('publicationPlan', {}).get('npm', {}).get('surfaces', [])
    plan['consumers'] = [{key: item.get(key) for key in ('repository', 'manifest', 'lock')}
                         for item in manifest.get('cee', {}).get('consumers', [])]
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


@contextmanager
def timed_stage(state, stage):
    manifest, _ = state.read_current_manifest()
    records = list(manifest.get('stageTimings', []))
    record = {'stage': stage, 'attempt': 1 + sum(item['stage'] == stage for item in records),
              'startedAt': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'workload': workload_signature(manifest)}
    index = len(records)
    records.append(record)
    state.update_current_manifest({'stageTimings': records})
    waits = {}
    token = _current_waits.set(waits)
    started = time.monotonic()
    status = 'interrupted'
    try:
        yield
        status = 'complete'
    except Exception:
        status = 'failed'
        raise
    finally:
        elapsed = max(0, time.monotonic() - started)
        _current_waits.reset(token)
        manifest, _ = state.read_current_manifest()
        records = list(manifest.get('stageTimings', []))
        records[index] = {**record, 'status': status, 'elapsedSeconds': elapsed,
                          'ciWaitSeconds': waits.get('ci', 0), 'retryWaitSeconds': waits.get('retry', 0),
                          'executionSeconds': max(0, elapsed - sum(waits.values())),
                          'completedAt': datetime.now(timezone.utc).isoformat()}
        state.update_current_manifest({'stageTimings': records})


def timing_summary(manifest):
    rows = {}
    for record in manifest.get('stageTimings', []):
        row = rows.setdefault(record['stage'], {'stage':record['stage'], 'attempts':0,
            'elapsedSeconds':0, 'executionSeconds':0, 'ciWaitSeconds':0, 'retryWaitSeconds':0,
            'incomplete':False, 'workloads':set()})
        row['attempts'] += 1
        row['workloads'].add(record.get('workload'))
        if record.get('status') == 'running' or 'elapsedSeconds' not in record:
            row['incomplete'] = True
        for key in ('elapsedSeconds', 'executionSeconds', 'ciWaitSeconds', 'retryWaitSeconds'):
            row[key] += record.get(key, 0)
    return rows


def render_timings(manifest, console, baseline=None):
    rows = timing_summary(manifest)
    if not rows:
        console.print('Stage timings: unavailable in this older ledger; no execution/wait split was recorded.')
        return
    previous = timing_summary(baseline or {})
    console.print('Stage timings (seconds; execution includes builds, HTTP calls and CI probes; waits are measured sleeps):')
    for stage, row in rows.items():
        comparison = ''
        old = previous.get(stage)
        if old and not (old['incomplete'] or row['incomplete']) and row['workloads'] == old['workloads'] and None not in row['workloads']:
            comparison = f"; execution delta {row['executionSeconds'] - old['executionSeconds']:+.1f}s vs {baseline.get('releaseVersion')}"
        elif baseline:
            comparison = '; comparison unavailable (missing/incomplete evidence or different workload)'
        console.print(f"{stage}: {row['attempts']} attempt(s), execution {row['executionSeconds']:.1f}, "
                      f"CI wait {row['ciWaitSeconds']:.1f}, retry wait {row['retryWaitSeconds']:.1f}, "
                      f"wall {row['elapsedSeconds']:.1f}" + (' [incomplete]' if row['incomplete'] else '') + comparison, markup=False)
    for record in manifest.get('stageTimings', []):
        console.print(f"  {record['stage']} attempt {record['attempt']}: {record['status']}, "
                      f"wall {record.get('elapsedSeconds', 0):.1f}s", markup=False)
    console.print('Workload-matched comparisons are observations, not controlled benchmarks; source and network conditions may differ.')


def recorded_timings(state, release_version):
    """The timings of one release: its ledger while it is current, its kept record after."""
    for path in (state.manifest_path(release_version), state.timing_record_path(release_version)):
        if path.is_file():
            return json.loads(path.read_text())
    raise ReleaseError(f'no ledger or timing record holds release {release_version}')


def prior_timing_manifest(state, current):
    candidates = []
    paths = [*(state.root / 'releases').glob('*.json'), *(state.root / 'timings').glob('*.json')]
    for path in paths:
        try:
            value = json.loads(path.read_text())
            if value.get('releaseVersion') != current.get('releaseVersion') and value.get('stageTimings'):
                candidates.append(value)
        except (OSError, ValueError):
            continue
    return max(candidates, key=lambda item: item.get('startedAt', ''), default=None)

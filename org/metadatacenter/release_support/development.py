"""Verify next-development artifacts and exact integrated CI before accepting a release."""
import copy
import datetime as dt
from pathlib import Path
import subprocess
import time
from org.metadatacenter.github_ci import probe_exact_commit, latest_runs_by_name, run_url, GithubCIProbeError
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.acceptance import ReleaseAcceptance
from org.metadatacenter.release_support.output import console
from org.metadatacenter.util.InvocationContext import invocation_environment

AGGREGATORS = {'cedar-libraries': 'ci.yml', 'cedar-project': 'ci.yml'}


class DevelopmentVerifier:
    def __init__(self, state, *, acceptance=None, runner=None, probe=None, sleeper=time.sleep,
                 polls=90, delay=20, environment=None):
        self.state = state
        self.environment = dict(invocation_environment() if environment is None else environment)
        self.acceptance = acceptance or ReleaseAcceptance(state, environment=self.environment)
        self.runner = runner or subprocess.run
        self.probe = probe or probe_exact_commit
        self.sleeper, self.polls, self.delay = sleeper, polls, delay

    def save(self, evidence):
        self.state.update_current_manifest({'developmentVerification': copy.deepcopy(evidence)})

    def run(self, manifest):
        # Snapshot byte verification and validate-local also prove that the regenerated
        # audit hashes match the next-development locks. No ledger or lock is repaired here.
        self.acceptance._remote_state_still_holds(manifest)
        self.acceptance._published_artifacts_still_hold(manifest)
        checks = self.acceptance._next_development_can_seed_train(manifest)
        evidence = copy.deepcopy(manifest.get('developmentVerification') or {})
        evidence['checks'] = checks
        evidence.setdefault('dispatches', {})
        evidence.setdefault('repositories', {})
        records = manifest.get('remoteIntegration', {}).get('completedTasks', {})
        if not records:
            raise ReleaseError('No integrated commits to verify')
        workspace = Path(manifest['versionPreparation']['nextDevelopment']['workspace'])
        # The intent is durable before dispatch: an ambiguous response is reconciled
        # with exact-SHA dispatch runs, never blindly dispatched a second time.
        for record in records.values():
            repo, revision = record['repository'], record['develop']['commit']
            if repo in evidence['dispatches'] and evidence['dispatches'][repo]['revision'] != revision:
                raise ReleaseError(f'{repo} dispatch evidence names a different revision')
            if repo not in AGGREGATORS or repo in evidence['dispatches']:
                continue
            dispatch = {'revision': revision, 'requestedAt': dt.datetime.now(dt.timezone.utc).isoformat()}
            evidence['dispatches'][repo] = dispatch
            self.save(evidence)
            result = self.runner(['gh', 'workflow', 'run', AGGREGATORS[repo], '--repo',
                f'metadatacenter/{repo}', '--ref', 'develop'],
                text=True, capture_output=True, check=False, env=self.environment)
            if result.returncode:
                raise ReleaseError(f'{repo} verification dispatch has an uncertain outcome; '
                    'resume to reconcile its exact-commit workflow run before dispatching again')
        for attempt in range(self.polls):
            pending = []
            failures = []
            for record in records.values():
                repo, revision = record['repository'], record['develop']['commit']
                if not (workspace / repo).is_dir():
                    raise ReleaseError(f'Missing prepared development repository: {repo}')
                workflows = workspace / repo / '.github' / 'workflows'
                if not any(workflows.glob('*.y*ml')):
                    evidence['repositories'][repo] = {'revision': revision, 'status': 'no-workflow',
                        'reason': 'No workflow in the prepared source; release build gates apply'}
                    continue
                try:
                    probe = self.probe(repo, revision, runner=self.runner, environment=self.environment,
                                       delays=(), reporter=None)
                except GithubCIProbeError as error:
                    raise ReleaseError(str(error)) from error
                runs = [r for r in probe.runs if r.get('head_sha') == revision
                        and r.get('head_branch') == 'develop'
                        and r.get('event') in {'push', 'workflow_dispatch'}
                        and r.get('path') != '.github/workflows/build-train.yml']
                if repo in AGGREGATORS:
                    requested = dt.datetime.fromisoformat(evidence['dispatches'][repo]['requestedAt'])
                    # GitHub timestamps have second precision; use the request's second.
                    requested = requested.replace(microsecond=0)
                    runs = [r for r in runs if r.get('event') == 'workflow_dispatch'
                            and r.get('path') == '.github/workflows/' + AGGREGATORS[repo]
                            and dt.datetime.fromisoformat(r['created_at'].replace('Z','+00:00')) >= requested]
                latest = latest_runs_by_name(runs)
                evidence['repositories'][repo] = {'revision': revision, 'runs': [
                    {'id': r.get('id'), 'url': run_url(r), 'status': r.get('status'),
                     'conclusion': r.get('conclusion')} for r in latest.values()]}
                if not latest:
                    pending.append(f'{repo}: waiting for exact-commit CI')
                for name, run in latest.items():
                    if run.get('status') != 'completed':
                        pending.append(f'{repo} {name}: {run.get("status")}')
                    elif run.get('conclusion') not in {'success', 'skipped', 'neutral'}:
                        failures.append(f'{repo} {name}: {run.get("conclusion")} {run_url(run)}')
            self.save(evidence)
            if failures:
                raise ReleaseError('Next-development CI failed:\n' + '\n'.join(failures))
            if not pending:
                # A sibling move during an aggregator run invalidates its proof.
                self.acceptance._remote_state_still_holds(manifest)
                evidence['completedAt'] = dt.datetime.now(dt.timezone.utc).isoformat()
                self.save(evidence)
                return evidence
            console.print(f'Development CI: {len(pending)} pending; ' + '; '.join(pending), markup=False)
            if attempt + 1 < self.polls:
                self.sleeper(self.delay)
        raise ReleaseError('Next-development CI is still pending after bounded waiting; '
                           'release resume continues verification without rebuilding or redispatching')


def verify_active_development(state, verifier=None):
    manifest, _ = state.read_current_manifest()
    if manifest.get('phase') == 'development-verified':
        return manifest
    if manifest.get('phase') not in {'artifacts-published', 'verifying-development', 'development-verification-failed'}:
        raise ReleaseError(f'Cannot verify development from {manifest.get("phase")}')
    state.update_current_manifest({'phase':'verifying-development', 'failure':None})
    try:
        evidence = (verifier or DevelopmentVerifier(state)).run(manifest)
    except (ReleaseError, OSError) as error:
        state.update_current_manifest({'phase':'development-verification-failed','failure':str(error)})
        raise ReleaseError(str(error)) from error
    completed, _ = state.update_current_manifest({'phase':'development-verified',
        'developmentVerification':evidence, 'failure':None})
    return completed

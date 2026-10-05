"""Explain the controller's next actions without executing checks or changing state."""
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.lifecycle import release_stages, _next_release_stage, REWIND_TO_FRONTENDS
from org.metadatacenter.release_support.preflight import ReleasePreflight, recorded_acceptances

SECTIONS = {'frontends':'frontendPreparation', 'versions':'versionPreparation',
    'builds':'buildValidation', 'local-refs':'localRefs', 'snapshots':'snapshotPublication',
    'remotes':'remoteIntegration', 'artifacts':'artifactPublication',
    'development':'developmentVerification', 'acceptance':'acceptance'}
ACTIONS = {
    'frontends': 'Prepare isolated frontend workspaces and required builds.',
    'versions': 'Stamp release and next-development versions in isolated workspaces.',
    'builds': 'Verify completed build logs/artifacts; build and test unfinished tasks.',
    'local-refs': 'Verify recorded refs; create the remaining local release/development refs.',
    'snapshots': 'Verify published snapshot evidence; publish remaining next-development Maven tasks.',
    'remotes': 'Verify recorded remote refs; integrate and push remaining repositories.',
    'artifacts': 'Verify source, build and published-byte evidence; publish remaining release artifacts.',
    'development': 'Recheck source/artifacts and baselines; wait for exact-source development CI.',
    'acceptance': 'Recheck final remote refs and immutable artifacts, then conclude the release.',
}


def resume_preview(manifest):
    phase = manifest.get('phase')
    if phase == 'abandoned':
        raise ReleaseError('An abandoned release cannot be resumed')
    if phase == 'accepted':
        return {'phase': phase, 'checks': [], 'stages': [],
                'note': 'Already accepted. Resume only reconciles the active release slot if needed.'}
    next_stage = _next_release_stage(manifest)
    stages = release_stages(manifest)
    start = next(index for index, stage in enumerate(stages) if stage.name == next_stage)
    rows = []
    for index, stage in enumerate(stages):
        evidence = manifest.get(SECTIONS[stage.name]) or {}
        completed = sorted(evidence.get('completedTasks', {}))
        rows.append({'stage': stage.name, 'action': 'reuse completed phase' if index < start else ACTIONS[stage.name],
                     'recordedTasks': completed, 'pending': index >= start})
    return {'phase': phase, 'checks': ReleasePreflight.resume_check_names(manifest), 'stages': rows,
            'note': ('Partial version preparation requires a fresh frontend/version attempt; its partial output is not reused.'
                     if phase in REWIND_TO_FRONTENDS else
                     'Recorded evidence is provisional until resume verifies it. In-flight tasks without completion evidence are retried.')}


def render_resume_preview(manifest, console):
    preview = resume_preview(manifest)
    console.print(f"Resume preview — {manifest.get('releaseVersion')} ({preview['phase']})", markup=False)
    console.print(preview['note'], markup=False)
    console.print('Checks to repeat: ' + (', '.join(name.removeprefix('check_') for name in preview['checks']) or 'none'), markup=False)
    red_develop, main_only = recorded_acceptances(manifest)
    accepted = ([f'red develop {repository}={run_id}' for repository, run_id in sorted(red_develop.items())]
                + [f'main-only {repository}' for repository in sorted(main_only)])
    if accepted:
        console.print('Acceptances recorded at start, applied again: ' + ', '.join(accepted), markup=False)
    for row in preview['stages']:
        console.print(f"{row['stage']}: {row['action']}", markup=False)
        if row['recordedTasks']:
            console.print('  Recorded tasks to preserve/reverify: ' + ', '.join(row['recordedTasks']), markup=False)
    console.print('No checks executed, no files changed, no builds or publications started.')

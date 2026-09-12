"""CEDAR train status."""
from __future__ import annotations
from org.metadatacenter.util.BuildTrain import BuildTrain
import datetime as dt
import re
import time
from org.metadatacenter.train_support import output as _output_component
from org.metadatacenter.train_support import policy as _policy_component
from org.metadatacenter.train_support import survey as _survey_component
from org.metadatacenter.train_support import workflow as _workflow_component


def _job_state(job):
    conclusion = job.get('conclusion')
    if conclusion in {'success', 'neutral'}:
        return 'done'
    if conclusion == 'skipped':
        return 'skipped'
    if conclusion:
        return 'failed'
    if job.get('status') == 'in_progress':
        return 'running'
    return 'queued'


def _group_summary(jobs, total):
    counts = {state: 0 for state in ('done', 'running', 'queued', 'failed', 'skipped')}
    for job in jobs:
        counts[_job_state(job)] += 1
    unseen = max(0, total - len(jobs))
    counts['queued'] += unseen
    pieces = [f"{counts['done']}/{total} done"]
    pieces.extend(
        f'{counts[state]} {state}'
        for state in ('running', 'queued', 'failed', 'skipped')
        if counts[state]
    )
    return ', '.join(pieces)


def _workflow_summary(payload):
    jobs = payload.get('jobs') or []
    named = [(str(job.get('name', '')).lower(), job) for job in jobs]

    def first(*needles):
        return next((job for name, job in named if any(item in name for item in needles)), None)

    def one(job):
        return _job_state(job) if job else 'queued'

    maven = first('publish-maven')
    npm = [job for name, job in named if re.search(r'npm [123]/3', name)]
    docker_plan = first('record-docker-plan')
    docker = [
        job for name, job in named
        if any(name.startswith(prefix) for prefix in (
            'java-base', 'microservice-base', 'infrastructure',
            'microservices', 'frontends',
        ))
    ]
    verify = first('verify-docker-train')
    workflow_state = payload.get('conclusion') or payload.get('status') or 'unknown'
    return (
        f'Workflow {workflow_state} | Maven {one(maven)} | '
        f'npm {_group_summary(npm, 3)} | Docker plan {one(docker_plan)} | '
        f'images {_group_summary(docker, 31)} | verify {one(verify)}'
    )


def _failed_subcheck(payload):
    for job in payload.get('jobs') or []:
        if job.get('conclusion') not in _policy_component.FAILED_CONCLUSIONS:
            continue
        for step in job.get('steps') or []:
            if step.get('conclusion') in _policy_component.FAILED_CONCLUSIONS:
                return f"{job.get('name', 'unknown job')} — {step.get('name', 'unknown step')}"
        return str(job.get('name') or 'unknown job')
    return None


def _active_subcheck(payload):
    for job in payload.get('jobs') or []:
        if job.get('status') != 'in_progress':
            continue
        for step in job.get('steps') or []:
            if step.get('status') == 'in_progress':
                return f"{job.get('name', 'unknown job')} — {step.get('name', 'unknown step')}"
        return str(job.get('name') or 'unknown job')
    return 'workflow is queued'


def _elapsed(seconds):
    minutes, remainder = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:d}:{minutes:02d}:{remainder:02d}'


def _render_stage_records(records):
    for label, path, state, error in records:
        color = {'recorded': 'green', 'pending': 'yellow', 'unavailable': 'red'}[state]
        _output_component.console.print(f'  {label}: [{color}]{state}[/{color}]')
        if state == 'recorded':
            _output_component.console.print(f'    {BuildTrain.browse_url(path)}', soft_wrap=True)
        elif error and state == 'unavailable':
            _output_component.console.print(f'    {error}', soft_wrap=True)


# The workflow concludes success a few seconds before the Docker completion record appears on
# the state branch. In that window neither "complete" nor "still running" holds, and without this
# the fall-through recommends resuming a train that is merely finishing: advice that would spend
# an immutable version. A bounded window keeps a train that genuinely stopped from hiding here.
COMPLETION_RECORD_GRACE_SECONDS = 180

# A verdict an operator can read at a glance: the count is the decision, the names are a sample.
RELEASABILITY_NAMES_SHOWN = 10


def _finishing(workflow):
    """Whether the workflow has just succeeded and its completion record may still be in flight."""
    if not workflow or workflow.get('conclusion') != 'success':
        return False
    finished = workflow.get('updatedAt')
    if not finished:
        return False
    try:
        ended = dt.datetime.fromisoformat(str(finished).replace('Z', '+00:00'))
    except ValueError:
        return False
    age = (dt.datetime.now(dt.timezone.utc) - ended).total_seconds()
    return 0 <= age <= COMPLETION_RECORD_GRACE_SECONDS


def _render_recovery(version, records, workflow):
    state = {label: value for label, _path, value, _error in records}
    active = workflow and workflow.get('status') in {'queued', 'in_progress', 'waiting', 'pending'}
    if state.get('Docker') == 'recorded':
        _output_component.console.print('[green]Decision: complete; do not resume or abandon this train.[/green]')
        _output_component.console.print('Publication: Maven, npm, and all 31 Docker images are verified.')
        return True
    if active:
        _output_component.console.print('[yellow]Decision: still running; do not dispatch another train.[/yellow]')
        return False
    if _finishing(workflow):
        _output_component.console.print(
            '[yellow]Decision: completing; the workflow succeeded and the completion record is '
            'not written yet. Read this status again in a few seconds.[/yellow]')
        return False
    if state.get('source') != 'recorded':
        _output_component.console.print('[yellow]Decision: no source state was recorded; use a new train ID.[/yellow]')
        _output_component.console.print('Publication: none can have started before source state is recorded.')
        _output_component.console.print('Recommended command: cedarcli publish train', soft_wrap=True)
        return False

    verified = [
        label for label in ('Maven', 'npm model', 'npm CEE', 'npm frontends', 'Docker')
        if state.get(label) == 'recorded'
    ]
    _output_component.console.print(
        '[yellow]Decision: source state is recorded and publication is incomplete; '
        'resume this ID if the source stays unchanged.[/yellow]'
    )
    if verified:
        _output_component.console.print('Verified publication stages: ' + ', '.join(verified))
    else:
        _output_component.console.print(
            'Publication may be partial; no major publication completion is recorded yet.')
    _output_component.console.print(
        f'Recommended command: cedarcli publish train --resume {version} --dry-run',
        soft_wrap=True,
    )
    _output_component.console.print(
        'If the correction changes source or train configuration, commit it and start a new '
        'train instead.')
    return False



def _render_releasability(version):
    """Whether a completed train can still back a release, asked while the answer can be acted on."""
    try:
        source = BuildTrain._read(f'trains/{version}.json')
        moved, unreadable, captured = _survey_component.releasability_survey(source)
    except ValueError as error:
        _output_component.console.print(f'Releasable: not checked ({error})')
        return
    if moved:
        _output_component.console.print(
            f'[red]Releasable: no; {len(moved)} of {captured} captured repositories have '
            f'advanced since this train was built.[/red]')
        shown = moved[:RELEASABILITY_NAMES_SHOWN]
        listed = ', '.join(shown)
        if len(moved) > len(shown):
            listed += f', and {len(moved) - len(shown)} more'
        _output_component.console.print('  ' + listed, soft_wrap=True)
        _output_component.console.print(
            '  A release stamps this train\'s exact commits, so it needs a new train.')
    elif unreadable:
        _output_component.console.print(
            f'[yellow]Releasable: unproven; {len(unreadable)} of {captured} repositories could '
            f'not be read: ' + ', '.join(unreadable) + '[/yellow]')
    else:
        _output_component.console.print(
            f'[green]Releasable: yes; all {captured} captured heads are unchanged.[/green]')


def status(version=None, watch=False):
    try:
        if version:
            selected = BuildTrain.validate(version)
        else:
            selected = _workflow_component._newest_dispatched_train()
            _output_component.console.print(f'Newest dispatched train: {selected}')
    except ValueError as error:
        _output_component.console.print(f'[red]{error}[/red]')
        return 1

    try:
        workflow = _workflow_component._workflow_run(selected)
    except ValueError as error:
        workflow = None
        _output_component.console.print(f'[yellow]{error}[/yellow]')

    progress = None
    if workflow:
        run_id = workflow.get('databaseId')
        try:
            progress = _workflow_component._workflow_progress(run_id)
            previous = None
            watch_started = time.monotonic()
            last_report = watch_started - _policy_component.WATCH_HEARTBEAT_SECONDS
            while watch and progress.get('status') in {
                'queued', 'in_progress', 'waiting', 'pending',
            }:
                summary = _workflow_summary(progress)
                now = time.monotonic()
                heartbeat = now - last_report >= _policy_component.WATCH_HEARTBEAT_SECONDS
                if summary != previous or heartbeat:
                    detail = _active_subcheck(progress)
                    _output_component.console.print(
                        f'{summary} | active {detail} | '
                        f'elapsed {_elapsed(now - watch_started)}',
                        soft_wrap=True,
                    )
                    previous = summary
                    last_report = now
                time.sleep(10)
                progress = _workflow_component._workflow_progress(run_id)
            summary = _workflow_summary(progress)
            if summary != previous:
                _output_component.console.print(summary, soft_wrap=True)
            _output_component.console.print(f"Workflow: {progress.get('url') or workflow.get('url')}", soft_wrap=True)
            failure = _failed_subcheck(progress)
            if failure:
                _output_component.console.print(f'[red]Failed subcheck: {failure}[/red]')
        except KeyboardInterrupt:
            _output_component.console.print('[yellow]Stopped watching; the workflow is still running.[/yellow]')
            return 130
        except ValueError as error:
            _output_component.console.print(f'[yellow]{error}[/yellow]')

    records = _workflow_component._stage_records(selected)
    _output_component.console.print(f'Build train {selected}')
    _render_stage_records(records)
    _output_component.console.print(
        f'Manifest branch: {BuildTrain.STATE_BROWSE_URL}',
        soft_wrap=True,
    )
    if _render_recovery(selected, records, progress or workflow):
        _render_releasability(selected)
    return int(bool(progress and progress.get('conclusion') in _policy_component.FAILED_CONCLUSIONS))

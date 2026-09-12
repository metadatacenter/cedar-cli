"""CEDAR train workflow."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.BuildTrain import BuildTrain
import json
import re
import subprocess
from org.metadatacenter.train_support import policy as _policy_component


def _dispatched_run_id(result):
    output = '\n'.join(
        value for value in (result.stdout, result.stderr)
        if isinstance(value, str)
    )
    match = re.search(
        rf'https://github\.com/{re.escape(_policy_component.REPOSITORY)}/actions/runs/(\d+)',
        output,
    )
    return match.group(1) if match else None


def _stages(version):
    return (
        ('source', f'trains/{version}.json'),
        ('Maven', f'completed/{version}.json'),
        ('npm plan', f'npm/trains/{version}.json'),
        ('npm model', f'npm/model/completed/{version}.json'),
        ('npm CEE', f'npm/cee/completed/{version}.json'),
        ('npm frontends', f'npm/completed/{version}.json'),
        ('Docker plan', f'docker/trains/{version}.json'),
        ('Docker', f'docker/completed/{version}.json'),
    )


def _stage_records(version):
    records = []
    for label, path in _stages(version):
        try:
            BuildTrain._read(path)
            records.append((label, path, 'recorded', None))
        except ValueError as error:
            state = 'pending' if 'does not exist' in str(error) else 'unavailable'
            records.append((label, path, state, str(error)))
    return records


def _workflow_runs():
    command = [
        'gh', 'run', 'list', '--repo', _policy_component.REPOSITORY,
        '--workflow', _policy_component.WORKFLOW, '--limit', '100',
        '--json', 'databaseId,status,conclusion,url,displayTitle,createdAt,updatedAt',
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, env=invocation_environment())
    except OSError as error:
        raise ValueError(f'cannot inspect the build-train workflow: {error}') from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ValueError(
            'cannot inspect the build-train workflow'
            + (f': {detail[-1]}' if detail else ''))
    try:
        return json.loads(result.stdout or '[]')
    except json.JSONDecodeError as error:
        raise ValueError('GitHub CLI returned invalid workflow JSON') from error


def _newest_dispatched_train():
    """The train behind the most recently created build-train workflow run.

        The operator never chooses a train ID, so the one they mean is almost always the one
        just dispatched. Newest by creation time, not by ID: the IDs carry the development base
        version first, and 2.10 sorts before 2.9 as text.
        """
    dated = []
    for run in _workflow_runs():
        match = _policy_component.TRAIN_TITLE_RE.match(str(run.get('displayTitle', '')))
        if match:
            dated.append((str(run.get('createdAt', '')), match.group(1)))
    if not dated:
        raise ValueError(f'no dispatched build train was found in {_policy_component.WORKFLOW}')
    return max(dated)[1]


def _workflow_run(version):
    runs = _workflow_runs()
    prefix = f'Build train {version}'
    matches = [
        run for run in runs
        if run.get('displayTitle') == prefix
        or str(run.get('displayTitle', '')).startswith(prefix + ' (')
    ]
    return max(matches, key=lambda run: run.get('createdAt', '')) if matches else None


def _workflow_progress(run_id):
    command = [
        'gh', 'run', 'view', str(run_id), '--repo', _policy_component.REPOSITORY,
        '--json', 'status,conclusion,url,jobs',
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, env=invocation_environment())
    except OSError as error:
        raise ValueError(f'cannot inspect workflow run {run_id}: {error}') from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ValueError(
            f'cannot inspect workflow run {run_id}'
            + (f': {detail[-1]}' if detail else ''))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError('GitHub CLI returned invalid workflow-run JSON') from error


def _active_workflow_runs():
    command = [
        'gh', 'run', 'list', '--repo', _policy_component.REPOSITORY,
        '--workflow', _policy_component.WORKFLOW, '--limit', '20',
        '--json', 'databaseId,status,displayTitle',
        '--jq', '.[] | select(.status == "queued" or .status == "in_progress")'
                ' | [.databaseId, .status, .displayTitle] | @tsv',
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, env=invocation_environment())
    except OSError as error:
        raise ValueError(f'cannot inspect active build trains: {error}') from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ValueError(
            'cannot inspect active build trains'
            + (f': {detail[-1]}' if detail else ''))
    return [line for line in result.stdout.splitlines() if line.strip()]

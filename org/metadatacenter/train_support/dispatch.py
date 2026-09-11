"""CEDAR train dispatch."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.BuildTrain import BuildTrain
import shlex
import subprocess
from org.metadatacenter.train_support import output as _output_component
from org.metadatacenter.train_support import policy as _policy_component
from org.metadatacenter.train_support import preflight as _preflight_component
from org.metadatacenter.train_support import workflow as _workflow_component


def _dry_run(selected, resume, command):
    try:
        summary, _source = _preflight_component._preflight(selected, resume)
    except ValueError as error:
        _output_component.console.print(f'[red]{error}[/red]')
        return 1
    repository_count, model, cee, frontend_count, additional_count = summary

    _output_component.console.print('[bold]DRY RUN — no workflow will be dispatched[/bold]')
    _output_component.console.print(
        f'Train: {selected}'
        + ('' if resume else ' (prospective ID; not reserved)')
    )
    _output_component.console.print(f'Mode: {"resume" if resume else "new"}')
    _output_component.console.print('Preflight:')
    _output_component.console.print('  [green]OK[/green] GitHub authentication, workflow, and idle slot')
    _output_component.console.print(
        f'  [green]OK[/green] source capture configuration: '
        f'{repository_count} repositories from develop',
        soft_wrap=True,
    )
    _output_component.console.print(
        f'  [green]OK[/green] npm order: {model} → {cee} → '
        f'{frontend_count} frontends ({additional_count} additional CEE consumers)',
        soft_wrap=True,
    )
    _output_component.console.print(
        f'  [green]OK[/green] train ID is '
        f'{"recorded for resume" if resume else "available"}'
    )
    _output_component.console.print(
        '  [green]OK[/green] every local source repository is on synchronized develop, '
        'committed, and pushed'
    )
    _output_component.console.print(
        '  [green]OK[/green] read-only Nexus, Maven, npm, and Docker publication targets '
        '(credentials from environment or ~/.m2/settings.xml)'
    )

    if resume:
        next_stage = None
        for label, path in _workflow_component._stages(selected)[1:]:
            try:
                BuildTrain._read(path)
            except ValueError as error:
                if 'does not exist' in str(error):
                    next_stage = label
                    break
                _output_component.console.print(f'[red]{error}[/red]')
                return 1
        _output_component.console.print(f'Next incomplete stage: {next_stage or "none (train is complete)"}')

    _output_component.console.print('Would dispatch:', soft_wrap=True)
    _output_component.console.print(f'  {shlex.join(command)}', soft_wrap=True)
    _output_component.console.print('[green]No changes made.[/green]')
    return 0


def dispatch(resume=None, dry_run=False):
    try:
        selected = BuildTrain.validate(resume) if resume else BuildTrain.allocate()
    except (OSError, ValueError) as error:
        _output_component.console.print(f'[red]{error}[/red]')
        return 1

    command = [
        'gh', 'workflow', 'run', _policy_component.WORKFLOW,
        '--repo', _policy_component.REPOSITORY,
        # The workflow file is present on the default branch, while develop selects the exact
        # controller revision that is also captured as a source input by the train itself.
        '--ref', 'develop',
        '--field', f'version={selected}',
        '--field', f'resume={"true" if resume else "false"}',
    ]
    if dry_run:
        return _dry_run(selected, resume, command)

    try:
        _preflight_component._preflight(selected, resume)
    except ValueError as error:
        _output_component.console.print(f'[red]Build-train preflight failed: {error}[/red]')
        return 1

    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=invocation_environment(),
        )
    except OSError as error:
        _output_component.console.print(f'[red]Could not run GitHub CLI: {error}[/red]')
        return 1
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()
        if detail:
            _output_component.console.print(detail, markup=False)
        return result.returncode
    _output_component.console.print(f'[green]Dispatched build train {selected}.[/green]')
    run_id = _workflow_component._dispatched_run_id(result)
    if run_id:
        _output_component.console.print(
            f'https://github.com/{_policy_component.REPOSITORY}/actions/runs/{run_id}',
            soft_wrap=True,
        )
        _output_component.console.print(
            f'Compact live summary: cedarcli publish train-status {selected} --watch',
            soft_wrap=True,
        )
        _output_component.console.print(
            'Detailed GitHub output: '
            + shlex.join([
                'gh', 'run', 'watch', run_id,
                '--repo', _policy_component.REPOSITORY,
                '--compact',
                '--exit-status',
            ]),
            soft_wrap=True,
        )
    else:
        _output_component.console.print(
            '[yellow]GitHub CLI did not return the exact run ID.[/yellow]'
        )
        _output_component.console.print(
            f'Find it with: gh run list --repo {_policy_component.REPOSITORY} '
            f'--workflow {_policy_component.WORKFLOW}',
            soft_wrap=True,
        )
    return 0

"""CEDAR train reporting."""
from __future__ import annotations
from rich.table import Column, Table
from rich.text import Text
from org.metadatacenter.train_support import output as _output_component
from org.metadatacenter.train_support import policy as _policy_component
from org.metadatacenter.train_support import survey as _survey_component


def _report_open_work(findings):
    _output_component.console.print('[red]The train would not contain all of your work.[/red]')
    for finding in findings:
        _output_component.console.print(f'  {finding}')
    _output_component.console.print(
        'A train is built from metadatacenter/develop on GitHub. Commit and push, or stash, '
        'and dispatch again.')


def report_source_ci(show_all=False):
    """Show CI at every head a train would capture, and whether a train would refuse.

        A release advances `develop` in forty repositories at once, and a red run among them is
        otherwise discovered when the next train is attempted. This is the same probe the
        dispatch preflight runs, laid out as a table with the run to look at and, for a red run,
        the command that re-runs only its failed jobs.
        """
    try:
        verdicts = _survey_component.source_ci_survey()
    except ValueError as error:
        _output_component.console.print(f'[red]{error}[/red]')
        return 1
    styles = {
        'green': 'green', 'red': 'red', 'pending': 'yellow',
        'missing': 'yellow', 'advisory': 'yellow', 'error': 'red',
    }
    shown = [verdict for verdict in verdicts if show_all or verdict.state != 'green']
    if shown:
        table = Table(
            Column('Repository', no_wrap=True),
            Column('Commit', no_wrap=True),
            Column('Workflow'),
            Column('Result'),
            Column('Run', overflow='fold'),
            title='CI at the develop commits a train would capture',
        )
        for verdict in shown:
            table.add_row(
                verdict.repository,
                verdict.revision[:8],
                verdict.workflow or '',
                Text(verdict.detail, style=styles[verdict.state]),
                verdict.url,
            )
        _output_component.console.print(table)
    counts = {
        state: sum(1 for verdict in verdicts if verdict.state == state)
        for state in _policy_component.SourceCIVerdict.STATES
    }
    _output_component.console.print(
        f"{counts['green']} green, {counts['red']} red, {counts['pending']} pending, "
        f"{counts['missing']} without a run, {counts['advisory']} without a workflow, "
        f"{counts['error']} unreadable"
    )
    reruns = [
        verdict for verdict in verdicts
        if verdict.state == 'red' and verdict.run_id and verdict.run_repository
    ]
    if reruns:
        _output_component.console.print(
            'If a red run failed for a reason its commit did not cause, re-run only its '
            'failed jobs:')
        for verdict in reruns:
            _output_component.console.print(
                f'  gh run rerun {verdict.run_id} --failed --repo {verdict.run_repository}',
                soft_wrap=True,
            )
    blocking = [verdict for verdict in verdicts if verdict.blocks_a_train]
    if blocking:
        _output_component.console.print(
            f'[red]A train would refuse: {len(blocking)} verdict(s) are not green.[/red]')
        return 1
    _output_component.console.print(
        '[green]Every captured head with a workflow is green; a train may be dispatched.'
        '[/green]')
    return 0


def report_main_ahead(show_all=False):
    """Show every repository whose main carries file content develop does not.

        A release publishes what a train captured from develop, so a change that reached main
        alone is replaced by it. The release gate refuses such a source, but only once someone
        is already mid-release. Asked between releases, this is a question with a cheap answer:
        port the change, or confirm develop dropped it deliberately.
        """
    try:
        verdicts = _survey_component.main_ahead_survey()
    except ValueError as error:
        _output_component.console.print(f'[red]{error}[/red]')
        return 1
    styles = {'merged': 'green', 'ahead': 'red', 'missing': 'yellow', 'error': 'red'}
    shown = [verdict for verdict in verdicts if show_all or verdict.state != 'merged']
    if shown:
        table = Table(
            Column('Repository', no_wrap=True),
            Column('Files', no_wrap=True),
            Column('What main changed and develop did not', overflow='fold'),
            title='Content on main that a release would replace',
        )
        for verdict in shown:
            table.add_row(
                verdict.repository,
                str(len(verdict.paths)) if verdict.paths else '',
                Text(verdict.detail, style=styles[verdict.state]),
            )
        _output_component.console.print(table)
    unmerged = [verdict for verdict in verdicts if verdict.unmerged]
    _output_component.console.print(
        f'{sum(1 for v in verdicts if v.state == "merged")} merged, {len(unmerged)} ahead, '
        f'{sum(1 for v in verdicts if v.state == "missing")} unreadable branches, '
        f'{sum(1 for v in verdicts if v.state == "error")} in error'
    )
    if unmerged:
        _output_component.console.print(
            '[red]A release would replace this content. Port it to develop, then build a '
            'train from that source.[/red]')
        return 1
    _output_component.console.print('[green]No repository carries content on main alone.[/green]')
    return 0

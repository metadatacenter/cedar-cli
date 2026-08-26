import json
import os
from pathlib import Path
import shlex
import subprocess

from rich.console import Console

from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.util.Util import Util


console = Console()


class BuildTrainWorker:
    WORKFLOW = 'build-train.yml'
    REPOSITORY = 'metadatacenter/cedar-development'

    @staticmethod
    def _stages(version):
        return (
            ('source', f'trains/{version}.json'),
            ('Maven', f'completed/{version}.json'),
            ('npm plan', f'npm/trains/{version}.json'),
            ('npm', f'npm/completed/{version}.json'),
            ('Docker plan', f'docker/trains/{version}.json'),
            ('Docker', f'docker/completed/{version}.json'),
        )

    @classmethod
    def status(cls, version):
        try:
            selected = BuildTrain.validate(version)
        except ValueError as error:
            console.print(f'[red]{error}[/red]')
            return 1
        console.print(f'Build train {selected}')
        for label, path in cls._stages(selected):
            recorded = False
            try:
                BuildTrain._read(path)
                state = '[green]recorded[/green]'
                recorded = True
            except ValueError as error:
                state = (
                    '[yellow]pending[/yellow]'
                    if 'does not exist' in str(error)
                    else '[red]unavailable[/red]'
                )
            console.print(f'  {label}: {state}')
            if recorded:
                console.print(f'    {BuildTrain.browse_url(path)}', soft_wrap=True)
        console.print(
            f'Manifest branch: {BuildTrain.STATE_BROWSE_URL}',
            soft_wrap=True,
        )
        return 0

    @classmethod
    def _configuration_summary(cls):
        cedar_home = Util.cedar_home or os.environ.get('CEDAR_HOME')
        if not cedar_home:
            raise ValueError('CEDAR_HOME is not set')
        ops = Path(cedar_home) / 'cedar-development' / 'ops'
        try:
            build = json.loads((ops / 'build-train.json').read_text(encoding='utf-8'))
            frontend = json.loads((ops / 'frontend-train.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f'cannot read build-train configuration: {error}') from error

        repositories = build.get('repositories', [])
        if not repositories or len(repositories) != len(set(repositories)):
            raise ValueError('build-train repositories must be a non-empty unique list')
        if build.get('organization') != 'metadatacenter' or build.get('sourceBranch') != 'develop':
            raise ValueError('build-train source must be metadatacenter develop')

        model = frontend.get('model', {}).get('repository')
        cee = frontend.get('cee', {}).get('repository')
        frontends = frontend.get('frontends', [])
        additional = frontend.get('additionalCeeConsumers', [])
        required = [model, cee]
        required.extend(item.get('repository') for item in frontends)
        required.extend(item.get('repository') for item in additional)
        missing = sorted({repository for repository in required if repository not in repositories})
        if missing:
            raise ValueError(
                'frontend train references repositories absent from the source train: '
                + ', '.join(missing)
            )
        if not model or not cee or model == cee:
            raise ValueError('frontend train must declare distinct TypeScript model and CEE repositories')
        for key in ('id', 'image', 'npmVersionVariable'):
            values = [item.get(key) for item in frontends]
            if not values or any(not value for value in values) or len(values) != len(set(values)):
                raise ValueError(f'frontend train {key} values must be present and unique')
        return len(repositories), model, cee, len(frontends), len(additional)

    @classmethod
    def _github_preflight(cls):
        checks = (
            (
                ['gh', 'auth', 'status', '--hostname', 'github.com'],
                'GitHub CLI authentication',
            ),
            (
                [
                    'gh', 'api', '--method', 'GET',
                    f'repos/{cls.REPOSITORY}/contents/.github/workflows/{cls.WORKFLOW}',
                    '-f', 'ref=develop', '--silent',
                ],
                f'{cls.WORKFLOW} on develop',
            ),
        )
        for command, description in checks:
            try:
                result = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            except OSError as error:
                raise ValueError(f'cannot run GitHub CLI: {error}') from error
            if result.returncode:
                detail = (result.stderr or result.stdout).strip().splitlines()
                suffix = f': {detail[-1]}' if detail else ''
                raise ValueError(f'{description} failed{suffix}')
            console.print(f'  [green]OK[/green] {description}')

    @classmethod
    def _dry_run(cls, selected, resume, command):
        source_path = f'trains/{selected}.json'
        try:
            source = BuildTrain._read(source_path)
            source_exists = True
        except ValueError as error:
            if 'does not exist' not in str(error):
                console.print(f'[red]{error}[/red]')
                return 1
            source = None
            source_exists = False

        if resume:
            if not source_exists:
                console.print(f'[red]train {selected} has no recorded source manifest[/red]')
                return 1
            if source.get('version') != selected:
                console.print(f'[red]source manifest does not describe {selected}[/red]')
                return 1
        elif source_exists:
            console.print(f'[red]train {selected} already exists; use --resume {selected}[/red]')
            return 1

        try:
            repository_count, model, cee, frontend_count, additional_count = (
                cls._configuration_summary()
            )
        except ValueError as error:
            console.print(f'[red]{error}[/red]')
            return 1

        console.print('[bold]DRY RUN — no workflow will be dispatched[/bold]')
        console.print(f'Train: {selected}')
        console.print(f'Mode: {"resume" if resume else "new"}')
        console.print('Preflight:')
        try:
            cls._github_preflight()
        except ValueError as error:
            console.print(f'  [red]FAIL[/red] {error}')
            return 1
        console.print(
            f'  [green]OK[/green] source capture configuration: '
            f'{repository_count} repositories from develop',
            soft_wrap=True,
        )
        console.print(
            f'  [green]OK[/green] npm order: {model} → {cee} → '
            f'{frontend_count} frontends ({additional_count} additional CEE consumers)',
            soft_wrap=True,
        )
        console.print(
            f'  [green]OK[/green] train ID is '
            f'{"recorded for resume" if resume else "available"}'
        )

        if resume:
            next_stage = None
            for label, path in cls._stages(selected)[1:]:
                try:
                    BuildTrain._read(path)
                except ValueError as error:
                    if 'does not exist' in str(error):
                        next_stage = label
                        break
                    console.print(f'[red]{error}[/red]')
                    return 1
            console.print(f'Next incomplete stage: {next_stage or "none (train is complete)"}')

        console.print('Would dispatch:', soft_wrap=True)
        console.print(f'  {shlex.join(command)}', soft_wrap=True)
        console.print('[green]No changes made.[/green]')
        return 0

    @classmethod
    def dispatch(cls, resume=None, dry_run=False):
        try:
            selected = BuildTrain.validate(resume) if resume else BuildTrain.allocate()
        except (OSError, ValueError) as error:
            console.print(f'[red]{error}[/red]')
            return 1

        command = [
            'gh', 'workflow', 'run', cls.WORKFLOW,
            '--repo', cls.REPOSITORY,
            # The workflow file is present on the default branch, while develop selects the exact
            # controller revision that is also captured as a source input by the train itself.
            '--ref', 'develop',
            '--field', f'version={selected}',
            '--field', f'resume={"true" if resume else "false"}',
        ]
        if dry_run:
            return cls._dry_run(selected, resume, command)
        try:
            result = subprocess.run(command, text=True, check=False)
        except OSError as error:
            console.print(f'[red]Could not run GitHub CLI: {error}[/red]')
            return 1
        if result.returncode:
            return result.returncode
        console.print(f'[green]Dispatched build train {selected}.[/green]')
        console.print(
            f'Follow it with: gh run list --repo {cls.REPOSITORY} '
            f'--workflow {cls.WORKFLOW}'
        )
        return 0

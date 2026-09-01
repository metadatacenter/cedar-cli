import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from rich.console import Console

from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.util.Util import Util


console = Console()


class BuildTrainWorker:
    WORKFLOW = 'build-train.yml'
    REPOSITORY = 'metadatacenter/cedar-development'

    @classmethod
    def _open_work(cls):
        """Local work in a train source repository that GitHub has not got.

        A train captures its sources from `metadatacenter/develop` on GitHub, so anything left
        uncommitted, or committed and not pushed, is simply absent from it. Nothing says so: the
        train reports success, its images are built and verified, and the change someone believed
        they were shipping is not in any of them. Refusing costs a second; the alternative is found
        later, if at all.

        Untracked files do not count, for the reason the release preflight gives: they are ordinary
        in a development tree. A modified tracked file is work someone may believe is in the train.

        A repository that is not checked out here holds no local work by definition, so it is not a
        finding — the train reads GitHub, not this machine.
        """
        cedar_home = Util.cedar_home or os.environ.get('CEDAR_HOME')
        if not cedar_home:
            raise ValueError('CEDAR_HOME is not set')
        ops = Path(cedar_home) / 'cedar-development' / 'ops'
        try:
            build = json.loads((ops / 'build-train.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f'cannot read build-train configuration: {error}') from error

        findings = []
        for repository in build.get('repositories', []):
            root = Path(cedar_home) / repository
            if not (root / '.git').exists():
                continue
            code, dirty, _ = cls._git(root, 'status', '--porcelain', '--untracked-files=no')
            if code != 0:
                findings.append(f'{repository} is not a readable git repository')
                continue
            if dirty:
                count = len(dirty.splitlines())
                findings.append(
                    f'{repository} has {count} uncommitted change(s), which the train cannot see')
            code, ahead, _ = cls._git(root, 'rev-list', '--count', 'origin/develop..develop')
            if code == 0 and ahead.isdigit() and int(ahead) > 0:
                findings.append(
                    f'{repository} has {ahead} unpushed commit(s) on develop, '
                    'which the train cannot see')
        return findings

    @classmethod
    def _source_alignment(cls):
        """Require every local source checkout to describe the remote train source exactly."""
        cedar_home = Util.cedar_home or os.environ.get('CEDAR_HOME')
        if not cedar_home:
            raise ValueError('CEDAR_HOME is not set')
        ops = Path(cedar_home) / 'cedar-development' / 'ops'
        try:
            build = json.loads((ops / 'build-train.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f'cannot read build-train configuration: {error}') from error

        findings = []
        for repository in build.get('repositories', []):
            root = Path(cedar_home) / repository
            if not (root / '.git').exists():
                continue
            code, branch, _ = cls._git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
            if code != 0:
                continue
            if branch != 'develop':
                findings.append(f'{repository} is on {branch}, not develop')
            code, local, _ = cls._git(root, 'rev-parse', 'refs/heads/develop')
            if code != 0:
                findings.append(f'{repository} has no local develop branch')
                continue
            code, remote, detail = cls._git(
                root, 'ls-remote', '--heads', 'origin', 'refs/heads/develop')
            if code != 0 or not remote:
                findings.append(
                    f'{repository} cannot read origin/develop'
                    + (f': {detail.splitlines()[-1]}' if detail else ''))
                continue
            remote_sha = remote.split()[0]
            if local != remote_sha:
                findings.append(
                    f'{repository} local develop is {local[:8]}, but GitHub develop is '
                    f'{remote_sha[:8]}')
        return findings

    @staticmethod
    def _git(root, *arguments):
        completed = subprocess.run(
            ['git', '-C', str(root), *arguments],
            capture_output=True, text=True, check=False)
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()

    @classmethod
    def _report_open_work(cls, findings):
        console.print('[red]The train would not contain all of your work.[/red]')
        for finding in findings:
            console.print(f'  {finding}')
        console.print(
            'A train is built from metadatacenter/develop on GitHub. Commit and push, or stash, '
            'and dispatch again.')

    @classmethod
    def _dispatched_run_id(cls, result):
        output = '\n'.join(
            value for value in (result.stdout, result.stderr)
            if isinstance(value, str)
        )
        match = re.search(
            rf'https://github\.com/{re.escape(cls.REPOSITORY)}/actions/runs/(\d+)',
            output,
        )
        return match.group(1) if match else None

    @staticmethod
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
            docker = json.loads((ops / 'docker-train.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f'cannot read build-train configuration: {error}') from error

        repositories = build.get('repositories', [])
        if not repositories or len(repositories) != len(set(repositories)):
            raise ValueError('build-train repositories must be a non-empty unique list')
        if build.get('organization') != 'metadatacenter' or build.get('sourceBranch') != 'develop':
            raise ValueError('build-train source must be metadatacenter develop')
        maven = build.get('mavenRepositories', [])
        if (
            not maven or len(maven) != len(set(maven))
            or not set(maven).issubset(repositories)
        ):
            raise ValueError('Maven repositories must be a non-empty unique source subset')
        phases = build.get('phases', [])
        phase_names = [item.get('name') for item in phases if isinstance(item, dict)]
        phase_repositories = [item.get('repository') for item in phases if isinstance(item, dict)]
        if (
            not phases or len(phase_names) != len(phases)
            or any(not name for name in phase_names)
            or len(phase_names) != len(set(phase_names))
            or any(repository not in maven for repository in phase_repositories)
        ):
            raise ValueError('build-train Maven phases must be named, unique, and use Maven repositories')
        required_artifacts = build.get('requiredArtifacts', [])
        if (
            not required_artifacts or len(required_artifacts) != len(set(required_artifacts))
            or any(not isinstance(item, str) or not item for item in required_artifacts)
        ):
            raise ValueError('required Maven artifacts must be a non-empty unique list')

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
        groups = docker.get('groups', {})
        ordered_images = []
        for group in ('javaBase', 'microserviceBase', 'infrastructure', 'microservices', 'frontends'):
            images = groups.get(group, [])
            if not images or len(images) != len(set(images)) or any(not image for image in images):
                raise ValueError(f'Docker train {group} images must be present and unique')
            ordered_images.extend(images)
        if len(ordered_images) != 31 or len(set(ordered_images)) != 31:
            raise ValueError('Docker train must contain 31 unique core images')
        if {item['image'] for item in frontends} != set(groups['frontends']):
            raise ValueError('frontend and Docker train image sets differ')
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
    def _active_workflow_runs(cls):
        command = [
            'gh', 'run', 'list', '--repo', cls.REPOSITORY,
            '--workflow', cls.WORKFLOW, '--limit', '20',
            '--json', 'databaseId,status,displayTitle',
            '--jq', '.[] | select(.status == "queued" or .status == "in_progress")'
                    ' | [.databaseId, .status, .displayTitle] | @tsv',
        ]
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False)
        except OSError as error:
            raise ValueError(f'cannot inspect active build trains: {error}') from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise ValueError(
                'cannot inspect active build trains'
                + (f': {detail[-1]}' if detail else ''))
        return [line for line in result.stdout.splitlines() if line.strip()]

    @classmethod
    def _preflight(cls, selected, resume):
        source_path = f'trains/{selected}.json'
        try:
            source = BuildTrain._read(source_path)
            source_exists = True
        except ValueError as error:
            if 'does not exist' not in str(error):
                raise
            source = None
            source_exists = False
        if resume:
            if not source_exists:
                raise ValueError(f'train {selected} has no recorded source manifest')
            if source.get('version') != selected:
                raise ValueError(f'source manifest does not describe {selected}')
        elif source_exists:
            raise ValueError(f'train {selected} already exists; use --resume {selected}')

        summary = cls._configuration_summary()
        cls._github_preflight()
        active = cls._active_workflow_runs()
        if active:
            raise ValueError(
                'another build train is queued or running: ' + '; '.join(active))
        open_work = cls._open_work()
        if open_work:
            raise ValueError(
                'source repositories hold work the train cannot see: ' + '; '.join(open_work))
        alignment = cls._source_alignment()
        if alignment:
            raise ValueError(
                'local source checkouts do not match GitHub develop: ' + '; '.join(alignment))
        return summary, source

    @classmethod
    def _dry_run(cls, selected, resume, command):
        try:
            summary, _source = cls._preflight(selected, resume)
        except ValueError as error:
            console.print(f'[red]{error}[/red]')
            return 1
        repository_count, model, cee, frontend_count, additional_count = summary

        console.print('[bold]DRY RUN — no workflow will be dispatched[/bold]')
        console.print(f'Train: {selected}')
        console.print(f'Mode: {"resume" if resume else "new"}')
        console.print('Preflight:')
        console.print('  [green]OK[/green] GitHub authentication, workflow, and idle slot')
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
        console.print(
            '  [green]OK[/green] every local source repository is on synchronized develop, '
            'committed, and pushed'
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
            cls._preflight(selected, resume)
        except ValueError as error:
            console.print(f'[red]Build-train preflight failed: {error}[/red]')
            return 1

        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            console.print(f'[red]Could not run GitHub CLI: {error}[/red]')
            return 1
        if result.returncode:
            detail = (result.stderr or result.stdout or '').strip()
            if detail:
                console.print(detail, markup=False)
            return result.returncode
        console.print(f'[green]Dispatched build train {selected}.[/green]')
        run_id = cls._dispatched_run_id(result)
        if run_id:
            console.print(
                f'https://github.com/{cls.REPOSITORY}/actions/runs/{run_id}',
                soft_wrap=True,
            )
            console.print(
                f'Major-stage summary: cedarcli publish train-status {selected}',
                soft_wrap=True,
            )
            console.print(
                'Detailed live output: '
                + shlex.join([
                    'gh', 'run', 'watch', run_id,
                    '--repo', cls.REPOSITORY,
                    '--compact',
                    '--exit-status',
                ]),
                soft_wrap=True,
            )
        else:
            console.print(
                '[yellow]GitHub CLI did not return the exact run ID.[/yellow]'
            )
            console.print(
                f'Find it with: gh run list --repo {cls.REPOSITORY} '
                f'--workflow {cls.WORKFLOW}',
                soft_wrap=True,
            )
        return 0

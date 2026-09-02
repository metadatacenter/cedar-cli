import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

from rich.console import Console

from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.util.Util import Util
from org.metadatacenter.release_train import _environment_with_nexus_credentials


console = Console()


class BuildTrainWorker:
    WORKFLOW = 'build-train.yml'
    REPOSITORY = 'metadatacenter/cedar-development'
    FAILED_CONCLUSIONS = {
        'action_required', 'cancelled', 'failure', 'startup_failure', 'timed_out',
    }
    WATCH_HEARTBEAT_SECONDS = 60

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
    def _stage_records(cls, version):
        records = []
        for label, path in cls._stages(version):
            try:
                BuildTrain._read(path)
                records.append((label, path, 'recorded', None))
            except ValueError as error:
                state = 'pending' if 'does not exist' in str(error) else 'unavailable'
                records.append((label, path, state, str(error)))
        return records

    @classmethod
    def _workflow_run(cls, version):
        command = [
            'gh', 'run', 'list', '--repo', cls.REPOSITORY,
            '--workflow', cls.WORKFLOW, '--limit', '100',
            '--json', 'databaseId,status,conclusion,url,displayTitle,createdAt',
        ]
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False)
        except OSError as error:
            raise ValueError(f'cannot inspect the build-train workflow: {error}') from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise ValueError(
                'cannot inspect the build-train workflow'
                + (f': {detail[-1]}' if detail else ''))
        try:
            runs = json.loads(result.stdout or '[]')
        except json.JSONDecodeError as error:
            raise ValueError('GitHub CLI returned invalid workflow JSON') from error
        prefix = f'Build train {version}'
        matches = [
            run for run in runs
            if run.get('displayTitle') == prefix
            or str(run.get('displayTitle', '')).startswith(prefix + ' (')
        ]
        return max(matches, key=lambda run: run.get('createdAt', '')) if matches else None

    @classmethod
    def _workflow_progress(cls, run_id):
        command = [
            'gh', 'run', 'view', str(run_id), '--repo', cls.REPOSITORY,
            '--json', 'status,conclusion,url,jobs',
        ]
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False)
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

    @staticmethod
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

    @classmethod
    def _group_summary(cls, jobs, total):
        counts = {state: 0 for state in ('done', 'running', 'queued', 'failed', 'skipped')}
        for job in jobs:
            counts[cls._job_state(job)] += 1
        unseen = max(0, total - len(jobs))
        counts['queued'] += unseen
        pieces = [f"{counts['done']}/{total} done"]
        pieces.extend(
            f'{counts[state]} {state}'
            for state in ('running', 'queued', 'failed', 'skipped')
            if counts[state]
        )
        return ', '.join(pieces)

    @classmethod
    def _workflow_summary(cls, payload):
        jobs = payload.get('jobs') or []
        named = [(str(job.get('name', '')).lower(), job) for job in jobs]

        def first(*needles):
            return next((job for name, job in named if any(item in name for item in needles)), None)

        def one(job):
            return cls._job_state(job) if job else 'queued'

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
            f'npm {cls._group_summary(npm, 3)} | Docker plan {one(docker_plan)} | '
            f'images {cls._group_summary(docker, 31)} | verify {one(verify)}'
        )

    @classmethod
    def _failed_subcheck(cls, payload):
        for job in payload.get('jobs') or []:
            if job.get('conclusion') not in cls.FAILED_CONCLUSIONS:
                continue
            for step in job.get('steps') or []:
                if step.get('conclusion') in cls.FAILED_CONCLUSIONS:
                    return f"{job.get('name', 'unknown job')} — {step.get('name', 'unknown step')}"
            return str(job.get('name') or 'unknown job')
        return None

    @staticmethod
    def _active_subcheck(payload):
        for job in payload.get('jobs') or []:
            if job.get('status') != 'in_progress':
                continue
            for step in job.get('steps') or []:
                if step.get('status') == 'in_progress':
                    return f"{job.get('name', 'unknown job')} — {step.get('name', 'unknown step')}"
            return str(job.get('name') or 'unknown job')
        return 'workflow is queued'

    @staticmethod
    def _elapsed(seconds):
        minutes, remainder = divmod(max(0, int(seconds)), 60)
        hours, minutes = divmod(minutes, 60)
        return f'{hours:d}:{minutes:02d}:{remainder:02d}'

    @classmethod
    def _render_stage_records(cls, records):
        for label, path, state, error in records:
            color = {'recorded': 'green', 'pending': 'yellow', 'unavailable': 'red'}[state]
            console.print(f'  {label}: [{color}]{state}[/{color}]')
            if state == 'recorded':
                console.print(f'    {BuildTrain.browse_url(path)}', soft_wrap=True)
            elif error and state == 'unavailable':
                console.print(f'    {error}', soft_wrap=True)

    @classmethod
    def _render_recovery(cls, version, records, workflow):
        state = {label: value for label, _path, value, _error in records}
        active = workflow and workflow.get('status') in {'queued', 'in_progress', 'waiting', 'pending'}
        if state.get('Docker') == 'recorded':
            console.print('[green]Decision: complete; do not resume or abandon this train.[/green]')
            console.print('Publication: Maven, npm, and all 31 Docker images are verified.')
            return
        if active:
            console.print('[yellow]Decision: still running; do not dispatch another train.[/yellow]')
            return
        if state.get('source') != 'recorded':
            console.print('[yellow]Decision: no source state was recorded; use a new train ID.[/yellow]')
            console.print('Publication: none can have started before source state is recorded.')
            console.print('Recommended command: cedarcli publish train', soft_wrap=True)
            return

        verified = [
            label for label in ('Maven', 'npm model', 'npm CEE', 'npm frontends', 'Docker')
            if state.get(label) == 'recorded'
        ]
        console.print(
            '[yellow]Decision: source state is recorded and publication is incomplete; '
            'resume this ID if the source stays unchanged.[/yellow]'
        )
        if verified:
            console.print('Verified publication stages: ' + ', '.join(verified))
        else:
            console.print(
                'Publication may be partial; no major publication completion is recorded yet.')
        console.print(
            f'Recommended command: cedarcli publish train --resume {version} --dry-run',
            soft_wrap=True,
        )
        console.print(
            'If the correction changes source or train configuration, commit it and start a new '
            'train instead.')

    @classmethod
    def status(cls, version, watch=False):
        try:
            selected = BuildTrain.validate(version)
        except ValueError as error:
            console.print(f'[red]{error}[/red]')
            return 1

        try:
            workflow = cls._workflow_run(selected)
        except ValueError as error:
            workflow = None
            console.print(f'[yellow]{error}[/yellow]')

        progress = None
        if workflow:
            run_id = workflow.get('databaseId')
            try:
                progress = cls._workflow_progress(run_id)
                previous = None
                watch_started = time.monotonic()
                last_report = watch_started - cls.WATCH_HEARTBEAT_SECONDS
                while watch and progress.get('status') in {
                    'queued', 'in_progress', 'waiting', 'pending',
                }:
                    summary = cls._workflow_summary(progress)
                    now = time.monotonic()
                    heartbeat = now - last_report >= cls.WATCH_HEARTBEAT_SECONDS
                    if summary != previous or heartbeat:
                        detail = cls._active_subcheck(progress)
                        console.print(
                            f'{summary} | active {detail} | '
                            f'elapsed {cls._elapsed(now - watch_started)}'
                        )
                        previous = summary
                        last_report = now
                    time.sleep(10)
                    progress = cls._workflow_progress(run_id)
                summary = cls._workflow_summary(progress)
                if summary != previous:
                    console.print(summary)
                console.print(f"Workflow: {progress.get('url') or workflow.get('url')}", soft_wrap=True)
                failure = cls._failed_subcheck(progress)
                if failure:
                    console.print(f'[red]Failed subcheck: {failure}[/red]')
            except KeyboardInterrupt:
                console.print('[yellow]Stopped watching; the workflow is still running.[/yellow]')
                return 130
            except ValueError as error:
                console.print(f'[yellow]{error}[/yellow]')

        records = cls._stage_records(selected)
        console.print(f'Build train {selected}')
        cls._render_stage_records(records)
        console.print(
            f'Manifest branch: {BuildTrain.STATE_BROWSE_URL}',
            soft_wrap=True,
        )
        cls._render_recovery(selected, records, progress or workflow)
        return int(bool(progress and progress.get('conclusion') in cls.FAILED_CONCLUSIONS))

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
    def _publication_targets_preflight(cls):
        cedar_home = Util.cedar_home or os.environ.get('CEDAR_HOME')
        if not cedar_home:
            raise ValueError('CEDAR_HOME is not set')
        try:
            environment = _environment_with_nexus_credentials()
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError(f'cannot load Nexus credentials: {error}') from error
        if (
            not environment.get('BMIR_NEXUS_USERNAME')
            or not environment.get('BMIR_NEXUS_PASSWORD')
        ):
            raise ValueError(
                'Nexus credentials are unavailable; set BMIR_NEXUS_USERNAME and '
                'BMIR_NEXUS_PASSWORD or configure server bmir-nexus-releases in '
                '~/.m2/settings.xml')
        controller = Path(cedar_home) / 'cedar-development' / 'ops' / 'build_train.py'
        if not controller.is_file():
            raise ValueError(f'build-train controller is missing: {controller}')
        try:
            result = subprocess.run(
                [sys.executable, str(controller), 'probe-publication'],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            raise ValueError(f'cannot run publication-target preflight: {error}') from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise ValueError(
                'publication-target preflight failed'
                + (f': {detail[-1]}' if detail else ''))
        for line in result.stdout.splitlines():
            if line.startswith('OK '):
                console.print(f'  [green]OK[/green] {line.removeprefix("OK ")}')

    @classmethod
    def _local_configuration_preflight(cls):
        cedar_home = Util.cedar_home or os.environ.get('CEDAR_HOME')
        if not cedar_home:
            raise ValueError('CEDAR_HOME is not set')
        controller = Path(cedar_home) / 'cedar-development' / 'ops' / 'build_train.py'
        try:
            result = subprocess.run(
                [sys.executable, str(controller), 'validate-local',
                 '--workspace', str(cedar_home)],
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            raise ValueError(f'cannot run local train configuration preflight: {error}') from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise ValueError(
                'local train configuration preflight failed'
                + (f': {detail[-1]}' if detail else ''))

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
        if not resume:
            cls._local_configuration_preflight()
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
        cls._publication_targets_preflight()
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
        console.print(
            f'Train: {selected}'
            + ('' if resume else ' (prospective ID; not reserved)')
        )
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
        console.print(
            '  [green]OK[/green] read-only Nexus, Maven, npm, and Docker publication targets '
            '(credentials from environment or ~/.m2/settings.xml)'
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
                f'Compact live summary: cedarcli publish train-status {selected} --watch',
                soft_wrap=True,
            )
            console.print(
                'Detailed GitHub output: '
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

"""CEDAR train preflight."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment, process_environment
from org.metadatacenter import smoke_gate
from org.metadatacenter.npm_policy import npm_user_config_findings
from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.util.NexusCredentials import environment_with_nexus_credentials
from org.metadatacenter.util.Util import Util
from pathlib import Path, PurePosixPath
import json
import os
import subprocess
import sys
from org.metadatacenter import ci_env as _ci_env_component
from org.metadatacenter.train_support import output as _output_component
from org.metadatacenter.train_support import policy as _policy_component
from org.metadatacenter.train_support import survey as _survey_component
from org.metadatacenter.train_support import workflow as _workflow_component


def _configuration_summary():
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
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


def _github_preflight():
    checks = (
        (
            ['gh', 'auth', 'status', '--hostname', 'github.com'],
            'GitHub CLI authentication',
        ),
        (
            [
                'gh', 'api', '--method', 'GET',
                f'repos/{_policy_component.REPOSITORY}/contents/.github/workflows/{_policy_component.WORKFLOW}',
                '-f', 'ref=develop', '--silent',
            ],
            f'{_policy_component.WORKFLOW} on develop',
        ),
    )
    for command, description in checks:
        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                env=invocation_environment(),
            )
        except OSError as error:
            raise ValueError(f'cannot run GitHub CLI: {error}') from error
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            suffix = f': {detail[-1]}' if detail else ''
            raise ValueError(f'{description} failed{suffix}')
        _output_component.console.print(f'  [green]OK[/green] {description}')


def _publication_targets_preflight():
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
    if not cedar_home:
        raise ValueError('CEDAR_HOME is not set')
    try:
        environment = environment_with_nexus_credentials()
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
            env=process_environment(environment),
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
            _output_component.console.print(f'  [green]OK[/green] {line.removeprefix("OK ")}')


def _npm_configuration_preflight():
    configured = invocation_environment().get('NPM_CONFIG_USERCONFIG') \
        or invocation_environment().get('npm_config_userconfig')
    if configured:
        path = Path(configured).expanduser()
    else:
        try:
            result = subprocess.run(
                ['npm', 'config', 'get', 'userconfig'],
                text=True, capture_output=True, check=False,
                env=invocation_environment(),
            )
        except OSError as error:
            raise ValueError(f'cannot inspect npm user configuration: {error}') from error
        if result.returncode or not result.stdout.strip():
            detail = (result.stderr or '').strip().splitlines()
            raise ValueError(
                'cannot inspect npm user configuration'
                + (f': {detail[-1]}' if detail else ''))
        path = Path(result.stdout.strip()).expanduser()
    try:
        findings = npm_user_config_findings(path)
    except ValueError as error:
        raise ValueError(str(error)) from error
    blockers = [finding for finding in findings if finding.severity == 'fail']
    for finding in findings:
        style = 'red' if finding.severity == 'fail' else 'yellow'
        _output_component.console.print(f'  [{style}]npm config: {finding.message} in {path}[/{style}]')
        _output_component.console.print(f'    {finding.remedy}')
    if blockers:
        raise ValueError('npm user configuration can change publication authentication')


def _source_ci_preflight(source=None):
    failures = []
    for verdict in _survey_component.source_ci_survey(source):
        if verdict.state == 'advisory':
            _output_component.console.print(f'  [yellow]CI advisory: {verdict.detail}[/yellow]')
        elif verdict.state == 'error':
            failures.append(verdict.detail)
        elif verdict.blocks_a_train:
            suffix = f' ({verdict.url})' if verdict.url else ''
            failures.append(f'{verdict.repository}: {verdict.detail}{suffix}')
    if failures:
        raise ValueError('train source CI is not settled: ' + '; '.join(failures))


def _local_configuration_preflight():
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
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
            env=invocation_environment(),
        )
    except OSError as error:
        raise ValueError(f'cannot run local train configuration preflight: {error}') from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().removeprefix('ERROR: ')
        message = 'local train configuration preflight failed'
        if '\n' in detail:
            raise ValueError(f'{message}:\n{detail}')
        raise ValueError(f'{message}: {detail}' if detail else message)


def _smoke_gate_preflight(source=None):
    """Require a passing whole-stack smoke run against the exact source the train captures.

        A new train captures GitHub `develop`, which the alignment check requires the local checkouts
        to equal, so the local heads are the source to match; a resumed train has its recorded
        manifest. Either way the run is judged by the heads it tested, never by its age, so a run
        made before an unrelated repository moved still refuses: the train would carry that move.
        """
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
    if not cedar_home:
        raise ValueError('CEDAR_HOME is not set')
    recorded = source.get('repositories') if isinstance(source, dict) else None
    if recorded:
        expected = dict(recorded)
    else:
        try:
            repositories = smoke_gate.train_repositories(cedar_home)
        except smoke_gate.SmokeGateError as error:
            raise ValueError(str(error)) from error
        expected, _dirty, problems = smoke_gate.develop_heads(cedar_home, repositories)
        if problems:
            raise ValueError('source heads cannot be resolved: ' + '; '.join(problems))
    findings = smoke_gate.findings_for(cedar_home, expected)
    if findings:
        raise ValueError(
            'no passing whole-stack smoke run covers this source: ' + '; '.join(findings))


def _preflight(selected, resume):
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

    # Every stage runs even after one has refused, so the operator reads one report rather
    # than fixing a finding, rerunning, and meeting the next. The checks that need the GitHub
    # CLI are skipped once it has failed, because each would only repeat that failure.
    findings = []

    def settle(check, *arguments):
        try:
            return check(*arguments), True
        except ValueError as error:
            findings.append(str(error))
            return None, False

    summary, _ = settle(_configuration_summary)
    if not resume:
        settle(_local_configuration_preflight)
    _, github_ready = settle(_github_preflight)
    if github_ready:
        active, settled = settle(_workflow_component._active_workflow_runs)
        if settled and active:
            findings.append(
                'another build train is queued or running: ' + '; '.join(active))
    open_work, settled = settle(_survey_component._open_work)
    if settled and open_work:
        findings.append(
            'source repositories hold work the train cannot see: ' + '; '.join(open_work))
    alignment, settled = settle(_survey_component._source_alignment)
    if settled and alignment:
        findings.append(
            'local source checkouts do not match GitHub develop: ' + '; '.join(alignment))
    if github_ready:
        settle(_source_ci_preflight, source)
    settle(_smoke_gate_preflight, source)
    settle(_npm_configuration_preflight)
    settle(_publication_targets_preflight)
    _ci_env_preflight()
    if findings:
        raise ValueError(_preflight_failure(findings))
    return summary, source


def _ci_env_preflight():
    """Report CI environment drift without refusing the train.

    A copy missing an entry breaks only the repositories whose suites build that part of the
    configuration, so drift is not evidence that this train would fail, and refusing on it would
    have stopped legitimate trains. It is still worth saying here, because the next repository
    whose suite asks goes red at a moment nobody chose, and the operator is already looking at
    exactly this report.
    """
    try:
        code, output = _ci_env_component.ci_env_report()
    except ValueError as error:
        _output_component.console.print(f'  [yellow]CI environment not checked: {error}[/yellow]')
        return
    if not code:
        return
    drifted = [line.split()[1].rstrip(':') for line in output.splitlines()
               if line.strip().startswith('DRIFTED')]
    detail = (f"{len(drifted)} repositories carry a stale copy of ci-env-block.yml: "
              + ', '.join(drifted)) if drifted else 'ci-env-block.yml no longer matches the code'
    _output_component.console.print(f'  [yellow]CI environment advisory: {detail}[/yellow]')
    _output_component.console.print(
        '  [yellow]  Repair with cedarcli check ci-env --apply, outside a train.[/yellow]')


def _preflight_failure(findings):
    if len(findings) == 1:
        return findings[0]
    lines = []
    for finding in findings:
        first, *rest = finding.splitlines() or ['']
        lines.append(f'- {first}')
        lines.extend(f'  {line}' for line in rest)
    return f'{len(findings)} preflight findings:\n' + '\n'.join(lines)

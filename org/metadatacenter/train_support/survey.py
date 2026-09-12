"""CEDAR train survey."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from dataclasses import dataclass
from org.metadatacenter.github_ci import (
    GREEN_CONCLUSIONS,
    GithubCIProbeError,
    latest_runs_by_name,
    probe_exact_commit,
    run_url,
)
from org.metadatacenter.util.Util import Util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
import json
import os
import subprocess
from org.metadatacenter.train_support import git as _git_component
from org.metadatacenter.train_support import output as _output_component
from org.metadatacenter.train_support import policy as _policy_component


def _open_work():
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
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
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
        code, dirty, _ = _git_component._git(root, 'status', '--porcelain', '--untracked-files=no')
        if code != 0:
            findings.append(f'{repository} is not a readable git repository')
            continue
        if dirty:
            count = len(dirty.splitlines())
            findings.append(
                f'{repository} has {count} uncommitted change(s), which the train cannot see')
        code, ahead, _ = _git_component._git(root, 'rev-list', '--count', 'origin/develop..develop')
        if code == 0 and ahead.isdigit() and int(ahead) > 0:
            findings.append(
                f'{repository} has {ahead} unpushed commit(s) on develop, '
                'which the train cannot see')
    return findings


def _source_alignment():
    """Require every local source checkout to describe the remote train source exactly."""
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
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
        code, branch, _ = _git_component._git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
        if code != 0:
            continue
        if branch != 'develop':
            findings.append(f'{repository} is on {branch}, not develop')
        code, local, _ = _git_component._git(root, 'rev-parse', 'refs/heads/develop')
        if code != 0:
            findings.append(f'{repository} has no local develop branch')
            continue
        code, remote, detail = _git_component._git(
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


def releasability_survey(source, max_workers=12):
    """Which repositories have moved off the commits a train captured.

    A release stamps a train's exact commits and refuses any repository whose develop has left
    them, so a completed train stops being releasable the moment anything lands in one of the
    forty-four. That refusal is otherwise met at `release plan`, after the train has already
    been built and paid for. One ls-remote per repository answers it while the answer can still
    change what an operator does next.

    Returns the repositories that moved, those whose develop could not be read, and how many
    the train captured.
    """
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
    if not cedar_home:
        raise ValueError('CEDAR_HOME is not set')
    recorded = source.get('repositories', {}) if isinstance(source, dict) else {}
    if not recorded:
        raise ValueError('this train recorded no source repositories')

    def head(item):
        repository, _revision = item
        root = Path(cedar_home) / repository
        tracked = (root / '.git').exists()
        code, output, _detail = _git_component._git(
            root if tracked else Path(cedar_home),
            'ls-remote',
            'origin' if tracked else f'https://github.com/metadatacenter/{repository}.git',
            'refs/heads/develop',
        )
        if code != 0 or not output:
            return repository, None
        return repository, output.split()[0]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(head, sorted(recorded.items())))
    moved = [name for name, sha in results if sha is not None and sha != recorded[name]]
    unreadable = [name for name, sha in results if sha is None]
    return moved, unreadable, len(recorded)


def source_ci_survey(source=None, reporter=None):
    """One verdict per workflow at each train source repository's exact develop commit.

        A train captures `develop` on GitHub, so the question is asked of the remote head, or of
        the commit an existing source manifest recorded, never of the local checkout.
        """
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
    if not cedar_home:
        raise ValueError('CEDAR_HOME is not set')
    ops = Path(cedar_home) / 'cedar-development' / 'ops'
    try:
        build = json.loads((ops / 'build-train.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'cannot read build-train configuration: {error}') from error
    recorded = source.get('repositories', {}) if isinstance(source, dict) else {}
    report = reporter or (lambda message: _output_component.console.print(f'  [yellow]{message}[/yellow]'))
    verdicts = []
    for repository in build.get('repositories', []):
        root = Path(cedar_home) / repository
        revision = recorded.get(repository)
        if not revision:
            code, output, detail = _git_component._git(
                root if (root / '.git').exists() else Path(cedar_home),
                'ls-remote',
                'origin' if (root / '.git').exists() else
                f'https://github.com/metadatacenter/{repository}.git',
                'refs/heads/develop',
            )
            if code != 0 or not output:
                verdicts.append(_policy_component.SourceCIVerdict(
                    repository, '', '', 'error',
                    f'{repository}: cannot resolve develop'
                    + (f' ({detail.splitlines()[-1]})' if detail else '')))
                continue
            revision = output.split()[0]
        has_workflow = None
        if (root / '.git').exists():
            code, output, _detail = _git_component._git(
                root, 'ls-tree', '-r', '--name-only', revision, '--',
                '.github/workflows')
            if code == 0:
                has_workflow = bool(output.strip())
        if has_workflow is None:
            try:
                response = subprocess.run([
                    'gh', 'api',
                    f'repos/metadatacenter/{repository}/contents/.github/workflows?ref={revision}',
                ], text=True, capture_output=True, check=False, env=invocation_environment())
            except OSError as error:
                verdicts.append(_policy_component.SourceCIVerdict(
                    repository, revision, '', 'error',
                    f'{repository}: cannot inspect CI workflow contract ({error})'))
                continue
            has_workflow = response.returncode == 0
            if response.returncode and 'HTTP 404' not in (response.stderr or response.stdout):
                detail = (response.stderr or response.stdout or '').strip().splitlines()
                verdicts.append(_policy_component.SourceCIVerdict(
                    repository, revision, '', 'error',
                    f'{repository}: cannot inspect CI workflow contract '
                    f'({detail[-1] if detail else f"exit {response.returncode}"})'))
                continue
        if not has_workflow:
            verdicts.append(_policy_component.SourceCIVerdict(
                repository, revision, '', 'advisory',
                f'{repository} has no workflow contract; the train gates its outputs.'))
            continue
        try:
            probe = probe_exact_commit(
                repository, revision, reporter=report, environment=invocation_environment())
        except GithubCIProbeError as error:
            verdicts.append(_policy_component.SourceCIVerdict(repository, revision, '', 'error', str(error)))
            continue
        runs = list(probe.runs)
        if repository == 'cedar-development':
            runs = [
                record for record in runs
                if record.get('path') != '.github/workflows/build-train.yml'
            ]
        if not runs:
            verdicts.append(_policy_component.SourceCIVerdict(
                repository, revision, '', 'missing',
                f'no CI run for {revision[:8]} after bounded indexing grace'))
            continue
        for name, record in latest_runs_by_name(runs).items():
            status = record.get('status')
            conclusion = record.get('conclusion')
            owner = record.get('repository')
            run_repository = owner.get('full_name', '') if isinstance(owner, dict) else ''
            run_id = str(record.get('id') or '')
            url = run_url(record)
            if status != 'completed':
                verdicts.append(_policy_component.SourceCIVerdict(
                    repository, revision, name, 'pending',
                    f'{name} is {status or "pending"}', url, run_id, run_repository))
            elif conclusion not in GREEN_CONCLUSIONS:
                verdicts.append(_policy_component.SourceCIVerdict(
                    repository, revision, name, 'red',
                    f'{name} concluded {conclusion or "without a result"}',
                    url, run_id, run_repository))
            else:
                verdicts.append(_policy_component.SourceCIVerdict(
                    repository, revision, name, 'green',
                    f'{name} concluded {conclusion}', url, run_id, run_repository))
    return verdicts


@dataclass(frozen=True)
class BranchDivergence:
    """One repository's answer to whether main holds file content develop does not."""

    repository: str
    paths: tuple
    detail: str
    state: str  # 'merged', 'ahead', 'missing' or 'error'

    @property
    def unmerged(self) -> bool:
        return self.state == 'ahead'


def main_ahead_survey(repositories=None):
    """One verdict per train repository on what main carries that develop does not.

        The measure is changed files rather than commits. A release puts commits on main that
        never reach develop by design, so counting commits reports every repository after every
        release. What matters is content: a path main changed since the branches diverged and
        develop did not, which is what a release would replace.
        """
    cedar_home = Util.cedar_home or invocation_environment().get('CEDAR_HOME')
    if not cedar_home:
        raise ValueError('CEDAR_HOME is not set')
    if repositories is None:
        ops = Path(cedar_home) / 'cedar-development' / 'ops'
        try:
            build = json.loads((ops / 'build-train.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f'cannot read build-train configuration: {error}') from error
        repositories = build.get('repositories', [])
    verdicts = []
    for repository in repositories:
        root = Path(cedar_home) / repository
        if not (root / '.git').exists():
            verdicts.append(BranchDivergence(
                repository, (), 'not checked out on this machine', 'missing'))
            continue
        code, _output, detail = _git_component._git(
            root, 'fetch', '--quiet', '--no-tags', 'origin',
            '+refs/heads/main:refs/remotes/cedar-check/main',
            '+refs/heads/develop:refs/remotes/cedar-check/develop',
        )
        if code != 0:
            verdicts.append(BranchDivergence(
                repository, (), f'cannot fetch both branches'
                + (f' ({detail.splitlines()[-1]})' if detail else ''), 'missing'))
            continue
        code, base, detail = _git_component._git(
            root, 'merge-base',
            'refs/remotes/cedar-check/main', 'refs/remotes/cedar-check/develop')
        if code != 0 or not base:
            verdicts.append(BranchDivergence(
                repository, (), 'main and develop share no history', 'error'))
            continue
        changed = {}
        for branch in ('main', 'develop'):
            code, output, _detail = _git_component._git(
                root, 'diff', '--name-only', base.strip(),
                f'refs/remotes/cedar-check/{branch}')
            if code != 0:
                changed = None
                break
            changed[branch] = {line for line in output.splitlines() if line}
        if changed is None:
            verdicts.append(BranchDivergence(
                repository, (), 'cannot compare the two branches', 'error'))
            continue
        replaced = tuple(sorted(
            path for path in changed['main'] - changed['develop']
            if PurePosixPath(path).name not in _policy_component.VERSION_FILES
        ))
        if not replaced:
            verdicts.append(BranchDivergence(
                repository, (), 'develop carries everything main does', 'merged'))
            continue
        verdicts.append(BranchDivergence(
            repository, replaced, ', '.join(replaced[:4]), 'ahead'))
    return verdicts

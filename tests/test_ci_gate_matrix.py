"""Every state GitHub reports for a CI run, as each gate that reads CI judges it.

Three gates ask whether a source commit's CI allows it through: `cedarcli check ci`, the train's
dispatch preflight, and the release preflight. The first two share one survey of the commit's runs;
the release preflight reads the same runs with a loop of its own. Each state a run can be in must get
one answer from all three, so a commit a train refuses is not one a release then accepts.

The answer is the rule the gates agree on: a run that succeeded, was skipped or was neutral lets a
commit through, and any other conclusion, a run not yet completed, and a commit with no run at all
hold it back. A cancelled run answers nothing, since cancelling is something done to a workflow
and says nothing about the code: the newest run of the same workflow that finished answers in its
place, and a commit whose only run was cancelled is held back as one with no run is.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from org.metadatacenter.release_train import ReleasePreflight, ReleaseState
from org.metadatacenter.train_support import preflight as train_preflight
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.BuildTrainWorker import BuildTrainWorker
from tests.test_release_train import (
    FakeCommands,
    FakeCompletedProcess,
    FakeNexus,
    PREFLIGHT_ENVIRONMENT,
    manifest_fixture,
)

REVISION = 'a' * 40

# (status, conclusion), as the Actions API reports a run.
STATES = [
    ('completed', 'success'),
    ('completed', 'skipped'),
    ('completed', 'neutral'),
    ('completed', 'failure'),
    ('completed', 'timed_out'),
    ('completed', 'action_required'),
    ('completed', 'startup_failure'),
    ('completed', 'stale'),
    ('completed', None),
    ('completed', 'cancelled'),
    ('in_progress', None),
    ('queued', None),
    ('waiting', None),
    ('requested', None),
    (None, None),  # no run at all
]

PASSES = {'success', 'skipped', 'neutral'}

# The states the gates answer differently on purpose, until someone decides which answer is right.
KNOWN = {}


def expected(status, conclusion):
    return 'passes' if status == 'completed' and conclusion in PASSES else 'blocks'


def runs_for(status, conclusion):
    if status is None:
        return ()
    return ({'name': 'CI', 'status': status, 'conclusion': conclusion, 'id': 7,
             'event': 'push', 'head_branch': 'develop', 'head_sha': REVISION,
             'html_url': 'https://github.example/runs/7',
             'repository': {'full_name': 'metadatacenter/cedar-a'}},)


class CIGateMatrixTest(unittest.TestCase):
    """The three gates give each CI state one answer."""

    @staticmethod
    def _home(directory):
        ops = Path(directory) / 'cedar-development' / 'ops'
        ops.mkdir(parents=True)
        (ops / 'build-train.json').write_text(json.dumps({
            'organization': 'metadatacenter', 'sourceBranch': 'develop', 'repositories': ['cedar-a'],
        }), encoding='utf-8')
        folder = Path(directory) / 'cedar-a' / '.github' / 'workflows'
        folder.mkdir(parents=True)
        (folder / 'ci.yml').write_text('name: CI\n', encoding='utf-8')
        (Path(directory) / 'cedar-a' / '.git').mkdir(parents=True)

    def _survey(self, status, conclusion):
        """The survey `check ci` and the train's preflight both read."""
        def git(root, *arguments):
            if arguments[0] == 'ls-remote':
                return 0, f'{REVISION}\trefs/heads/develop', ''
            if arguments[0] == 'ls-tree':
                return 0, '.github/workflows/ci.yml', ''
            raise AssertionError(arguments)

        with tempfile.TemporaryDirectory() as directory:
            self._home(directory)
            with (
                patch.object(Util, 'cedar_home', directory),
                patch('org.metadatacenter.train_support.git._git', side_effect=git),
                patch('org.metadatacenter.train_support.survey.probe_exact_commit',
                      side_effect=lambda *_a, **_k: SimpleNamespace(runs=runs_for(status, conclusion))),
            ):
                return BuildTrainWorker.source_ci_survey()

    def _check_ci(self, verdicts):
        with patch('org.metadatacenter.train_support.survey.source_ci_survey', return_value=verdicts):
            return 'blocks' if BuildTrainWorker.report_source_ci(show_all=True) != 0 else 'passes'

    def _train_preflight(self, verdicts):
        with patch('org.metadatacenter.train_support.survey.source_ci_survey', return_value=verdicts):
            try:
                train_preflight._source_ci_preflight(REVISION)
            except ValueError:
                return 'blocks'
            return 'passes'

    def _release_preflight(self, status, conclusion):
        manifest = manifest_fixture()
        manifest['releaseRepositories'] = ['cedar-a']
        manifest['sourceRepositories'] = {'cedar-a': REVISION}
        commands = FakeCommands({
            ('git', '-C'): FakeCompletedProcess(stdout='.github/workflows/ci.yml'),
            ('gh', 'api'): FakeCompletedProcess(stdout=json.dumps({'workflow_runs': list(runs_for(status, conclusion))})),
        })
        preflight = ReleasePreflight(
            manifest,
            state=ReleaseState(root=Path(tempfile.gettempdir()) / 'ci-gate-matrix-state'),
            command_runner=commands,
            http=FakeNexus(),
            environment=dict(PREFLIGHT_ENVIRONMENT),
            ci_sleeper=lambda _delay: None,
            ci_delays=(),
        )
        findings = preflight.check_develop_is_green()
        return 'blocks' if any(finding.fatal for finding in findings) else 'passes'

    def test_every_gate_answers_every_ci_state_by_one_rule(self):
        differences = {}
        for status, conclusion in STATES:
            verdicts = self._survey(status, conclusion)
            answers = {
                'check ci': self._check_ci(verdicts),
                'train preflight': self._train_preflight(verdicts),
                'release preflight': self._release_preflight(status, conclusion),
            }
            rule = expected(status, conclusion)
            wrong = {gate: answer for gate, answer in answers.items() if answer != rule}
            if wrong:
                differences[(status, conclusion)] = wrong
        unlisted = {state: wrong for state, wrong in differences.items() if state not in KNOWN}
        closed = [state for state in KNOWN if state not in differences]
        self.assertEqual({}, unlisted)
        self.assertEqual([], closed, 'a listed divergence no longer occurs; remove it from KNOWN')


class CIGateEventMatrixTest(CIGateMatrixTest):
    """Which runs at a commit answer for develop.

    A commit can carry runs other than develop's own: a pull request whose head is that commit runs
    the same workflow under the same name. The next-development check counts only the runs a push or
    a dispatch on develop started. The gates here take the newest run of each name, so a pull
    request's run could stand in for develop's. A cancelled run answers for nothing, and the newest
    run that finished answers in its place.
    """

    CASES = [
        ('a push on develop that succeeded', [('push', 'develop', 'success')], 'passes'),
        ('a dispatch on develop that succeeded', [('workflow_dispatch', 'develop', 'success')], 'passes'),
        ('only a pull request that succeeded', [('pull_request', 'feature', 'success')], 'blocks'),
        ('a pull request that succeeded after develop failed',
         [('pull_request', 'feature', 'success'), ('push', 'develop', 'failure')], 'blocks'),
        ('a pull request that failed after develop succeeded',
         [('pull_request', 'feature', 'failure'), ('push', 'develop', 'success')], 'passes'),
        ('a cancelled run after one that succeeded',
         [('push', 'develop', 'cancelled'), ('push', 'develop', 'success')], 'passes'),
        ('a cancelled run after one that failed',
         [('push', 'develop', 'cancelled'), ('push', 'develop', 'failure')], 'blocks'),
        ('a cancelled dispatch after a push that succeeded',
         [('workflow_dispatch', 'develop', 'cancelled'), ('push', 'develop', 'success')], 'passes'),
    ]

    def _runs(self, case):
        return tuple({'name': 'CI', 'status': 'completed', 'conclusion': conclusion, 'id': 7 - index,
                      'event': event, 'head_branch': branch, 'head_sha': REVISION,
                      'html_url': f'https://github.example/runs/{7 - index}',
                      'repository': {'full_name': 'metadatacenter/cedar-a'}}
                     for index, (event, branch, conclusion) in enumerate(case))

    def test_every_gate_answers_every_ci_state_by_one_rule(self):
        differences = {}
        for name, case, rule in self.CASES:
            runs = self._runs(case)
            with patch('tests.test_ci_gate_matrix.runs_for', side_effect=lambda *_a: runs):
                verdicts = self._survey(None, None)
                answers = {
                    'check ci': self._check_ci(verdicts),
                    'train preflight': self._train_preflight(verdicts),
                    'release preflight': self._release_preflight(None, None),
                }
            wrong = {gate: answer for gate, answer in answers.items() if answer != rule}
            if wrong:
                differences[name] = wrong
        self.assertEqual({}, differences)


if __name__ == '__main__':
    unittest.main()

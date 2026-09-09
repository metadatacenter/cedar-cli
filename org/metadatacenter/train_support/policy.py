"""CEDAR train policy."""
from __future__ import annotations
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SourceCIVerdict:
    """CI at one train source repository's exact develop commit, as GitHub reports it.

    A repository yields one verdict per workflow it runs, or a single verdict saying why no
    workflow verdict exists: it has no workflow contract, GitHub has no run for the commit, or
    the state could not be read at all.
    """

    repository: str
    revision: str
    workflow: str
    state: str
    detail: str
    url: str = ''
    run_id: str = ''
    run_repository: str = ''

    STATES = ('green', 'red', 'pending', 'missing', 'advisory', 'error')

    @property
    def blocks_a_train(self):
        return self.state in {'red', 'pending', 'missing', 'error'}


WORKFLOW = 'build-train.yml'


REPOSITORY = 'metadatacenter/cedar-development'


TRAIN_TITLE_RE = re.compile(r'^Build train (\d+\.\d+\.\d+-dev\.\d{8}\.\d{4})')


FAILED_CONCLUSIONS = {
    'action_required', 'cancelled', 'failure', 'startup_failure', 'timed_out',
}


WATCH_HEARTBEAT_SECONDS = 60


VERSION_FILES = frozenset({
    'pom.xml', 'package.json', 'package-lock.json', 'npm-shrinkwrap.json',
})

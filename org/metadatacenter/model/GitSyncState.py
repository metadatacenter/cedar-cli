from enum import Enum
from typing import Optional


class GitSyncKind(Enum):
    CURRENT = 'current'
    BEHIND = 'behind'
    AHEAD = 'ahead'
    DIVERGED = 'diverged'
    NO_UPSTREAM = 'no upstream'
    DETACHED = 'detached'
    NOT_A_REPO = 'not a repo'


class GitSyncState:
    """
    Where a working copy stands against its remote-tracking branch.

    This exists so that a version mismatch can be attributed. A repository that is behind its
    remote and a repository that is current and still wrong have the same version on disk and
    different causes, and only the second is a defect in the estate.
    """

    def __init__(self, kind: GitSyncKind, branch: str = '', ahead: int = 0, behind: int = 0,
                 fetch_age_seconds: Optional[float] = None) -> None:
        self.kind = kind
        self.branch = branch
        self.ahead = ahead
        self.behind = behind
        self.fetch_age_seconds = fetch_age_seconds

    @staticmethod
    def tracking(branch: str, ahead: int, behind: int, fetch_age_seconds: Optional[float]):
        if ahead and behind:
            kind = GitSyncKind.DIVERGED
        elif behind:
            kind = GitSyncKind.BEHIND
        elif ahead:
            kind = GitSyncKind.AHEAD
        else:
            kind = GitSyncKind.CURRENT
        return GitSyncState(kind, branch, ahead, behind, fetch_age_seconds)

    @staticmethod
    def no_upstream(branch: str, fetch_age_seconds: Optional[float]):
        return GitSyncState(GitSyncKind.NO_UPSTREAM, branch, fetch_age_seconds=fetch_age_seconds)

    @staticmethod
    def detached(fetch_age_seconds: Optional[float]):
        return GitSyncState(GitSyncKind.DETACHED, fetch_age_seconds=fetch_age_seconds)

    @staticmethod
    def not_a_repo():
        return GitSyncState(GitSyncKind.NOT_A_REPO)

    def explains_a_stale_version(self) -> bool:
        """
        Whether this state accounts for an out-of-date version without anything being wrong.

        Only being behind does. A diverged copy holds local commits as well, so its version cannot
        be attributed to the remote alone and stays a finding for a person to read.
        """
        return self.kind == GitSyncKind.BEHIND

    def describe(self) -> str:
        if self.kind == GitSyncKind.BEHIND:
            return f"behind {self.behind}"
        if self.kind == GitSyncKind.AHEAD:
            return f"ahead {self.ahead}"
        if self.kind == GitSyncKind.DIVERGED:
            return f"ahead {self.ahead}, behind {self.behind}"
        if self.kind == GitSyncKind.CURRENT:
            return "current"
        return self.kind.value

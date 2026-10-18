"""Whether each repository's published snapshot still stands for its source.

A merge to develop is supposed to publish that repository's snapshot to Nexus, and every downstream
build resolves CEDAR artifacts from Nexus rather than from a checkout. So a repository whose commit
is on develop but whose snapshot never published is invisible in the place people look — its own
branch is green and its source is right — while every consumer builds against the artifact it
replaced.

That is not hypothetical. On 2026-08-29 a Dropwizard upgrade landed in cedar-parent, its deploy step
met a Nexus 500, and the snapshot never published. Every Java repository's CI failed from then on,
resolving a parent that did not manage a dependency the new poms named, and every build train failed
with it. Four unrelated regressions accumulated behind that one unpublished artifact before anyone
looked, because nothing compared what Nexus served against what develop held.

The comparison is the whole check: the timestamp Nexus records for a snapshot against the time of the
newest commit on develop that can change what the repository publishes. A commit that changes only
the repository's CI configuration or its documentation cannot, and a repository whose CI does not
publish, such as the cedar-project aggregator, which the release train publishes, would otherwise
report as behind after every such commit. This module is the decision, kept apart from the fetching
so it can be tested without a network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

# Maven writes this as UTC digits with no separators: 20260831221625.
LAST_UPDATED = re.compile(r"<lastUpdated>(\d{14})</lastUpdated>")

# How long a snapshot may lag its source before that counts as a failure to publish. CI has to
# check out, build, test and deploy before the snapshot appears, and a repository whose suite runs
# for half an hour is ordinary here, so this is generous on purpose: the condition worth alarming on
# lasted days, and a threshold that also catches an in-flight build would train people to ignore it.
DEFAULT_GRACE = timedelta(hours=2)


class SnapshotState(Enum):
    """What the published snapshot says about the source it was built from."""

    CURRENT = "current"
    BEHIND = "behind"
    ABSENT = "absent"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class SnapshotFinding:
    repository: str
    state: SnapshotState
    detail: str
    published_at: datetime | None = None
    committed_at: datetime | None = None

    @property
    def is_failure(self) -> bool:
        """Whether this finding should fail the check.

        UNREADABLE does not. A refused or unreachable Nexus is a fact about the network between here
        and it, and failing on that turns every outage into an alarm about the estate's source, which
        is the misdirection this check exists to remove. It is still reported.
        """
        return self.state in (SnapshotState.BEHIND, SnapshotState.ABSENT)


def changes_artifact(paths) -> bool:
    """Whether a commit that changed these files can change what its repository publishes.

    Only CI configuration under .github/ and Markdown outside the source tree are left out. Anything
    else counts, including a commit whose files are not known, so the check stays conservative.
    """
    paths = list(paths)
    return not paths or any(not _outside_artifact(path) for path in paths)


def _outside_artifact(path: str) -> bool:
    return path.startswith(".github/") or (path.lower().endswith(".md") and "/src/" not in "/" + path)


def parse_last_updated(metadata: str) -> datetime | None:
    """The UTC instant Nexus records for a snapshot, or None when the document does not carry one."""
    match = LAST_UPDATED.search(metadata or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_commit_time(iso_timestamp: str) -> datetime | None:
    """The commit time GitHub reports, normalised to UTC."""
    if not iso_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(iso_timestamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate(repository: str,
             version: str,
             published_at: datetime | None,
             committed_at: datetime | None,
             grace: timedelta = DEFAULT_GRACE,
             later_commits: int = 0) -> SnapshotFinding:
    """The verdict for one repository, given what Nexus serves and what develop holds.

    committed_at is the time of the newest commit on develop that can change the artifact, and
    later_commits counts the newer ones that change only CI configuration or documentation.

    An absent snapshot is a failure rather than an unknown: the repository is one the estate expects
    to publish, so nothing at that coordinate means nothing was ever published there, and a consumer
    asking for it gets whatever older version it can find.
    """
    source = "the head commit on develop" if later_commits == 0 else (
        "the newest commit on develop that changes it")
    if committed_at is None:
        return SnapshotFinding(repository, SnapshotState.UNREADABLE,
                               "the head commit on develop could not be read", published_at, None)
    if published_at is None:
        return SnapshotFinding(repository, SnapshotState.ABSENT,
                               f"Nexus serves no {version} snapshot", None, committed_at)
    if committed_at > published_at + grace:
        behind = committed_at - published_at
        return SnapshotFinding(
            repository, SnapshotState.BEHIND,
            f"published {_describe(behind)} before {source}",
            published_at, committed_at)
    if later_commits == 0:
        detail = "published after the head commit"
    else:
        later = "later commit changes" if later_commits == 1 else "later commits change"
        detail = (f"published after the newest commit that changes it; the {later_commits} {later} "
                  "only CI or documentation")
    return SnapshotFinding(repository, SnapshotState.CURRENT, detail, published_at, committed_at)


def _describe(gap: timedelta) -> str:
    """A gap in the largest unit that still says something useful about it."""
    hours = gap.total_seconds() / 3600
    if hours >= 48:
        return f"{gap.days} days"
    if hours >= 2:
        return f"{int(hours)} hours"
    return f"{int(gap.total_seconds() // 60)} minutes"

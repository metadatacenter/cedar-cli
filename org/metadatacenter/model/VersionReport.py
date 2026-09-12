from typing import Dict, List

from org.metadatacenter.model.GitSyncState import GitSyncState
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.VersionReportEntry import VersionReportEntry
from org.metadatacenter.model.VersionType import VersionType


def describe_age(seconds: float) -> str:
    """An age an operator reads at a glance rather than converting."""
    if seconds >= 3600:
        return f"{int(seconds / 3600)}h"
    return f"{max(1, int(seconds / 60))}m"


class VersionReport:

    def __init__(self) -> None:
        self.entries: List[VersionReportEntry] = []
        self.cnt_ok = 0
        self.cnt_nok = 0
        self.cnt_unknown = 0
        self.cnt_allowed_diff = 0
        self.cnt_stale = 0
        self.version_candidate = ''
        pass

    def add(self, repo: Repo, dir_suffix: str, file_name: str, version_type: VersionType, version: str,
            sync: GitSyncState = None):
        entry = VersionReportEntry(repo, dir_suffix, file_name, version_type, version, sync)
        self.entries.append(entry)
        pass

    def summarize(self):
        freq = {}
        for entry in self.entries:
            version = entry.version
            if version in freq:
                freq[version] += 1
            else:
                freq[version] = 1

        self.version_candidate = max(freq, key=freq.get)

        self.cnt_ok = 0
        self.cnt_nok = 0
        for entry in self.entries:
            if entry.repo.allow_different_version:
                repo_version_candidate = self.compute_candidate_for_repo(entry.repo)
                entry.compute_status(repo_version_candidate)
            else:
                entry.compute_status(self.version_candidate)
            self.cnt_ok += entry.cnt_ok
            self.cnt_nok += entry.cnt_nok
            self.cnt_unknown += entry.cnt_unknown
            self.cnt_allowed_diff += entry.cnt_allowed_diff
            self.cnt_stale += entry.cnt_stale

    def get_caption(self):
        caption = 'Target version: ' + self.version_candidate + '; ' + str(self.cnt_ok) + " versions matching"
        if self.cnt_nok > 0:
            caption += ", [red]" + str(self.cnt_nok) + " non-matching"
        if self.cnt_stale > 0:
            caption += ", [yellow]" + str(self.cnt_stale) + " behind the remote"
        if self.cnt_unknown > 0:
            caption += ", [yellow]" + str(self.cnt_unknown) + " unknown"
        if self.cnt_allowed_diff > 0:
            caption += ", [green]" + str(self.cnt_allowed_diff) + " allowed non-matching"
        return caption

    def get_remedy_lines(self, strict: bool = False) -> List[str]:
        """
        What to do about each kind of finding, named rather than left to be inferred.

        A stale clone and a real divergence produce the same version on disk, and the expensive
        misreading is to treat the first as the second: it invites re-running a release step that
        already succeeded.
        """
        lines = []
        stale_repos = self.repos_with(lambda entry: entry.cnt_stale > 0)
        if stale_repos:
            verdict = "This is fatal under --strict" if strict else "This does not fail the check"
            lines.append(
                f"{len(stale_repos)} repositories are behind their remote, which accounts for "
                f"{self.cnt_stale} of the versions above. The release published these; this workspace "
                f"has not pulled them. Run `cedarcli git pull`. {verdict}.")
        divergent_repos = self.repos_with(lambda entry: entry.cnt_nok > 0)
        if divergent_repos:
            lines.append(
                f"{len(divergent_repos)} repositories differ from the target while current with their "
                f"remote: " + ", ".join(sorted(divergent_repos)) + ".")
        partial_repos = sorted(self.repos_disagreeing_internally())
        if partial_repos:
            lines.append(
                "These repositories declare more than one version among their own files, which is a "
                "half-applied bump rather than a stale clone: " + ", ".join(partial_repos) + ".")
        stale = self.stale_fetch_repos()
        if stale:
            verdict = "This is fatal under --strict" if strict else "This does not fail the check"
            worst = ", ".join(f"{name} {describe_age(age)}" for name, age in stale[:3])
            more = f", and {len(stale) - 3} more" if len(stale) > 3 else ""
            subject = ("1 repository was" if len(stale) == 1
                       else f"{len(stale)} repositories were")
            lines.append(
                f"{subject} last fetched over {describe_age(self.STALE_FETCH_SECONDS)} ago "
                f"({worst}{more}), so their ahead/behind counts, and any target version computed "
                f"from them, describe an older remote. Run `cedarcli git fetch` first. {verdict}.")
        return lines

    def repos_with(self, predicate) -> List[str]:
        names = set()
        for entry in self.entries:
            if predicate(entry):
                names.add(entry.repo.name)
        return sorted(names)

    def repos_disagreeing_internally(self) -> List[str]:
        """Repositories whose own files do not agree with each other."""
        seen: Dict[str, set] = {}
        for entry in self.entries:
            if entry.version_type == VersionType.EMPTY or entry.version == '':
                continue
            seen.setdefault(entry.repo.name, set()).add(entry.version)
        return [name for name, versions in seen.items() if len(versions) > 1]

    # Session scale: long enough that ordinary work does not re-fetch constantly, short enough
    # that no gate certifies this workspace against yesterday's view of the remotes. Repositories
    # outside the release set are fetched rarely, so this names them rather than one oldest age.
    STALE_FETCH_SECONDS = 3600

    def stale_fetch_repos(self):
        """Repositories whose fetch is too old for their ahead/behind counts to mean anything."""
        oldest: Dict[str, float] = {}
        for entry in self.entries:
            age = entry.sync.fetch_age_seconds
            if age is None or age < self.STALE_FETCH_SECONDS:
                continue
            name = entry.repo.name
            if age > oldest.get(name, 0):
                oldest[name] = age
        return sorted(oldest.items(), key=lambda item: item[1], reverse=True)

    def oldest_fetch_age_seconds(self):
        ages = [entry.sync.fetch_age_seconds for entry in self.entries
                if entry.sync.fetch_age_seconds is not None]
        return max(ages) if ages else None

    def compute_candidate_for_repo(self, repo):
        freq = {}
        for entry in self.entries:
            if entry.repo == repo:
                version = entry.version
                if version in freq:
                    freq[version] += 1
                else:
                    freq[version] = 1
        repo_version_candidate = max(freq, key=freq.get)
        return repo_version_candidate

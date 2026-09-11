from typing import Dict, List

from org.metadatacenter.model.GitSyncState import GitSyncState
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.VersionReportEntry import VersionReportEntry
from org.metadatacenter.model.VersionType import VersionType


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
        age = self.oldest_fetch_age_seconds()
        if age is not None and self.cnt_stale == 0 and self.cnt_nok > 0:
            lines.append(
                f"Ahead/behind counts compare against the last fetch, up to {int(age / 3600)}h old here. "
                f"Run `cedarcli git fetch` for a current comparison.")
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

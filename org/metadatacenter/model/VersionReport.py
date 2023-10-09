from typing import List

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
        self.version_candidate = ''
        pass

    def add(self, repo: Repo, dir_suffix: str, file_name: str, version_type: VersionType, version: str):
        entry = VersionReportEntry(repo, dir_suffix, file_name, version_type, version)
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
            entry.compute_status(self.version_candidate)
            self.cnt_ok += entry.cnt_ok
            self.cnt_nok += entry.cnt_nok
            self.cnt_unknown += entry.cnt_unknown
            self.cnt_allowed_diff += entry.cnt_allowed_diff

    def get_caption(self):
        caption = 'Target version: ' + self.version_candidate + '; ' + str(self.cnt_ok) + " versions matching"
        if self.cnt_nok > 0:
            caption += ", [red]" + str(self.cnt_nok) + " non-matching"
        if self.cnt_unknown > 0:
            caption += ", [yellow]" + str(self.cnt_unknown) + " unknown"
        if self.cnt_allowed_diff > 0:
            caption += ", [green]" + str(self.cnt_allowed_diff) + " allowed non-matching"
        return caption

from typing import List

from org.metadatacenter.model.VersionDirReport import VersionDirReport


class VersionReport:

    def __init__(self) -> None:
        self.dirs: List[VersionDirReport] = []
        self.cnt_ok = 0
        self.cnt_nok = 0
        self.cnt_unknown = 0
        self.version_candidate = ''

    def add_dir(self, dir_report: VersionDirReport):
        self.dirs.append(dir_report)

    def summarize(self):
        freq = {}
        for dir in self.dirs:
            for version_type, version in dir.versions.items():
                if version in freq:
                    freq[version] += 1
                else:
                    freq[version] = 1

        self.version_candidate = max(freq, key=freq.get)

        self.cnt_ok = 0
        self.cnt_nok = 0
        for dir in self.dirs:
            dir.compute_status(self.version_candidate)
            self.cnt_ok += dir.cnt_ok
            self.cnt_nok += dir.cnt_nok
            self.cnt_unknown += dir.cnt_unknown

    def get_caption(self):
        caption = 'Target version: ' + self.version_candidate + '; ' + str(self.cnt_ok) + " versions matching"
        if self.cnt_nok > 0:
            caption += ", [red]" + str(self.cnt_nok) + " non-matching"
        if self.cnt_unknown > 0:
            caption += ", [yellow]" + str(self.cnt_unknown) + " unknown"
        return caption

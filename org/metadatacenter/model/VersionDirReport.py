from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.VersionType import VersionType


class VersionDirReport:
    def __init__(self, repo: Repo, dir_suffix: str, file_name: str):
        self.repo = repo
        self.dir_suffix = dir_suffix
        self.file_name = file_name
        self.status = ''
        self.versions: dict = {}
        self.cnt_ok = 0
        self.cnt_nok = 0
        self.cnt_unknown = 0
        self.marked_ok = False

    def add_version(self, version_type: VersionType, value: str):
        self.versions[version_type] = value

    def compute_status(self, reference_version):
        if self.marked_ok:
            self.cnt_ok = 1
            self.status = "✅"
            return
        
        for version_type, version in self.versions.items():
            if version == "" or version == reference_version:
                self.cnt_ok += 1
            else:
                self.cnt_nok += 1

        if len(self.versions) == 0:
            self.cnt_unknown += 1
        elif self.cnt_nok > 0:
            self.status = "❌"
        elif self.cnt_ok > 0:
            self.status = "✅"

    def mark_ok(self):
        self.marked_ok = True

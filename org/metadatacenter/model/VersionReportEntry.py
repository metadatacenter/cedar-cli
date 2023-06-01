from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.VersionType import VersionType


class VersionReportEntry:

    def __init__(self, repo: Repo, dir_suffix: str, file_name: str, version_type: VersionType, version: str) -> None:
        self.repo = repo
        self.dir_suffix = dir_suffix
        self.file_name = file_name
        self.version_type = version_type
        self.version = version

        self.status = ''
        self.cnt_ok = 0
        self.cnt_nok = 0
        self.cnt_unknown = 0

    def compute_status(self, reference_version):
        if self.version_type == VersionType.EMPTY:
            self.cnt_ok = 1
            self.status = "✅"
            return

        if self.version == "" or self.version == reference_version:
            self.cnt_ok += 1
        else:
            self.cnt_nok += 1

        if self.version == '':
            self.cnt_unknown += 1
        elif self.cnt_nok > 0:
            self.status = "❌"
        elif self.cnt_ok > 0:
            self.status = "✅"

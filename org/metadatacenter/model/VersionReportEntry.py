from org.metadatacenter.model.GitSyncState import GitSyncState
from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.VersionType import VersionType


class VersionReportEntry:

    def __init__(self, repo: Repo, dir_suffix: str, file_name: str, version_type: VersionType, version: str,
                 sync: GitSyncState = None) -> None:
        self.repo = repo
        self.dir_suffix = dir_suffix
        self.file_name = file_name
        self.version_type = version_type
        self.version = version
        self.sync = sync if sync is not None else GitSyncState.not_a_repo()

        self.status = ''
        self.cnt_ok = 0
        self.cnt_nok = 0
        self.cnt_unknown = 0
        self.cnt_allowed_diff = 0
        self.cnt_stale = 0

    def compute_status(self, reference_version):
        if self.version_type == VersionType.EMPTY:
            self.cnt_ok = 1
            self.status = "✅"
            return

        if self.repo.allow_different_version:
            if self.version == reference_version:
                self.cnt_allowed_diff += 1
            else:
                self.cnt_nok += 1
        else:
            if self.version == "" or self.version == reference_version:
                self.cnt_ok += 1
            else:
                self.cnt_nok += 1

        # A version behind the target in a checkout that is behind its remote is the remote's
        # version, not a divergence in the estate. Attribute it to the clone and keep it out of the
        # failure count, so that a workspace nobody pulled cannot fail this check.
        if self.cnt_nok > 0 and self.sync.explains_a_stale_version():
            self.cnt_stale = self.cnt_nok
            self.cnt_nok = 0

        if self.version == '':
            self.cnt_unknown += 1
        elif self.cnt_stale > 0:
            self.status = "⏳"
        elif self.cnt_nok > 0:
            self.status = "❌"
        elif self.cnt_ok > 0:
            self.status = "✅"
        elif self.cnt_allowed_diff > 0:
            self.status = "👍"

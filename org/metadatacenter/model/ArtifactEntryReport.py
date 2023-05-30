from org.metadatacenter.model.ArtifactStatus import ArtifactStatus
from org.metadatacenter.model.Repo import Repo


class ArtifactEntryReport:
    def __init__(self, repo: Repo, dir_suffix: str, url: str):
        self.repo = repo
        self.dir_suffix = dir_suffix
        self.url = url
        self.status = ArtifactStatus.UNKNOWN
        self.status_string = ''
        self.size = -1
        self.response_code = -1

    def set_status(self, status: ArtifactStatus):
        self.status = status
        if self.status == ArtifactStatus.NOT_NEEDED or self.status == ArtifactStatus.FOUND:
            self.status_string = "✅"
        elif self.status == ArtifactStatus.ERROR:
            self.status_string = "❌"

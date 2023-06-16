from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.worker import RepoWorker


class RepoCheckEntry:
    def __init__(self, repo: Repo, display_name: str, is_present: bool):
        self.repo = repo
        self.display_name = display_name
        self.is_present = is_present
        self.file_type = None
        self.repo_type = None
        self.recognized_as = None
        self.status_icon = None
        self.status = None

        if self.is_present:
            if self.repo is not None:
                self.status_icon = RepoWorker.STATUS_ICON_OK
                self.status = 'ok'
                self.file_type = RepoWorker.FS_TYPE_DIR
                self.recognized_as = RepoWorker.RECOGNIZED_AS_CEDAR_REPO
            else:
                self.status_icon = RepoWorker.STATUS_ICON_UNKNOWN
                self.status = 'unknown'
        else:
            self.status_icon = RepoWorker.STATUS_ICON_MISSING
            self.status = 'missing'

        if self.repo is not None:
            self.repo_type = repo.repo_type

    def set_recognized_as(self, recognized_as: str):
        self.recognized_as = recognized_as
        self.status_icon = RepoWorker.STATUS_ICON_OK
        self.status = 'ok'

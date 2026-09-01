from org.metadatacenter.worker.NativeWorker import NativeWorker
from org.metadatacenter.worker.Worker import Worker


class StopFrontendWorker(Worker):
    """The commands below take a frontend's bare name; the native controller names it ui-<name>."""

    @staticmethod
    def _stop(name: str):
        return NativeWorker.stop((f"ui-{name}",))

    @staticmethod
    def openview():
        return StopFrontendWorker._stop("openview")

    @staticmethod
    def monitoring():
        return StopFrontendWorker._stop("monitoring")

    @staticmethod
    def bridging():
        return StopFrontendWorker._stop("bridging")

    @staticmethod
    def content():
        return StopFrontendWorker._stop("content")

    @staticmethod
    def main():
        return StopFrontendWorker._stop("main")

    @staticmethod
    def workspace():
        return StopFrontendWorker._stop("workspace")

    @staticmethod
    def designer():
        return StopFrontendWorker._stop("designer")

    @staticmethod
    def split_frontends():
        return NativeWorker.stop(("ui-workspace", "ui-designer"))

    @staticmethod
    def all():
        return NativeWorker.stop(NativeWorker.FRONTENDS)

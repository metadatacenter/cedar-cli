from org.metadatacenter.worker.NativeWorker import NativeWorker
from org.metadatacenter.worker.Worker import Worker


class StartFrontendWorker(Worker):
    """The commands below take a frontend's bare name; the native controller names it ui-<name>."""

    @staticmethod
    def _start(name: str):
        return NativeWorker.start((f"ui-{name}",))

    @staticmethod
    def openview():
        return StartFrontendWorker._start("openview")

    @staticmethod
    def monitoring():
        return StartFrontendWorker._start("monitoring")

    @staticmethod
    def bridging():
        return StartFrontendWorker._start("bridging")

    @staticmethod
    def content():
        return StartFrontendWorker._start("content")

    @staticmethod
    def main():
        return StartFrontendWorker._start("main")

    @staticmethod
    def workspace():
        return StartFrontendWorker._start("workspace")

    @staticmethod
    def designer():
        return StartFrontendWorker._start("designer")

    @staticmethod
    def split_frontends():
        return NativeWorker.start(("ui-workspace", "ui-designer"))

    @staticmethod
    def all():
        return NativeWorker.start(NativeWorker.FRONTENDS)

from org.metadatacenter.worker.NativeWorker import NativeWorker
from org.metadatacenter.worker.Worker import Worker


class StartFrontendWorker(Worker):
    TARGETS = {
        "main": "frontend",
        "openview": "ui-openview",
        "monitoring": "ui-monitoring",
        "bridging": "ui-bridging",
        "content": "ui-content",
        "workspace": "workspace",
        "designer": "designer",
    }

    @staticmethod
    def _start(name: str):
        return NativeWorker.start((StartFrontendWorker.TARGETS[name],))

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
        return NativeWorker.start(("workspace", "designer"))

    @staticmethod
    def all():
        return NativeWorker.start(NativeWorker.FRONTENDS)

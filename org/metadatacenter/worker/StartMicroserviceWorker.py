from org.metadatacenter.worker.NativeWorker import NativeWorker
from org.metadatacenter.worker.Worker import Worker


class StartMicroserviceWorker(Worker):

    @staticmethod
    def all():
        return NativeWorker.start(NativeWorker.MICROSERVICES)

    @staticmethod
    def artifact():
        return StartMicroserviceWorker._start("artifact")

    @staticmethod
    def bridge():
        return StartMicroserviceWorker._start("bridge")

    @staticmethod
    def group():
        return StartMicroserviceWorker._start("group")

    @staticmethod
    def impex():
        return StartMicroserviceWorker._start("impex")

    @staticmethod
    def messaging():
        return StartMicroserviceWorker._start("messaging")

    @staticmethod
    def monitor():
        return StartMicroserviceWorker._start("monitor")

    @staticmethod
    def openview():
        return StartMicroserviceWorker._start("openview")

    @staticmethod
    def repo():
        return StartMicroserviceWorker._start("repo")

    @staticmethod
    def resource():
        return StartMicroserviceWorker._start("resource")

    @staticmethod
    def schema():
        return StartMicroserviceWorker._start("schema")

    @staticmethod
    def submission():
        return StartMicroserviceWorker._start("submission")

    @staticmethod
    def terminology():
        return StartMicroserviceWorker._start("terminology")

    @staticmethod
    def user():
        return StartMicroserviceWorker._start("user")

    @staticmethod
    def valuerecommender():
        return StartMicroserviceWorker._start("valuerecommender")

    @staticmethod
    def worker():
        return StartMicroserviceWorker._start("worker")

    @staticmethod
    def _start(service_name: str):
        return NativeWorker.start((service_name,))

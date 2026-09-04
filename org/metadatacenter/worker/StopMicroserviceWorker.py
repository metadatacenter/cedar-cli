from org.metadatacenter.worker.NativeWorker import NativeWorker
from org.metadatacenter.worker.Worker import Worker


class StopMicroserviceWorker(Worker):

    @staticmethod
    def all():
        return NativeWorker.stop(NativeWorker.MICROSERVICES)

    @staticmethod
    def artifact():
        return StopMicroserviceWorker._stop("artifact")

    @staticmethod
    def bridge():
        return StopMicroserviceWorker._stop("bridge")

    @staticmethod
    def group():
        return StopMicroserviceWorker._stop("group")

    @staticmethod
    def impex():
        return StopMicroserviceWorker._stop("impex")

    @staticmethod
    def messaging():
        return StopMicroserviceWorker._stop("messaging")

    @staticmethod
    def monitor():
        return StopMicroserviceWorker._stop("monitor")

    @staticmethod
    def openview():
        return StopMicroserviceWorker._stop("openview")

    @staticmethod
    def repo():
        return StopMicroserviceWorker._stop("repo")

    @staticmethod
    def resource():
        return StopMicroserviceWorker._stop("resource")

    @staticmethod
    def schema():
        return StopMicroserviceWorker._stop("schema")

    @staticmethod
    def submission():
        return StopMicroserviceWorker._stop("submission")

    @staticmethod
    def terminology():
        return StopMicroserviceWorker._stop("terminology")

    @staticmethod
    def user():
        return StopMicroserviceWorker._stop("user")

    @staticmethod
    def valuerecommender():
        return StopMicroserviceWorker._stop("valuerecommender")

    @staticmethod
    def worker():
        return StopMicroserviceWorker._stop("worker")

    @staticmethod
    def _stop(service_name: str):
        return NativeWorker.stop((service_name,))

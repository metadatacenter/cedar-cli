from enum import Enum


class DockerDeploymentMode(str, Enum):
    FULL = "full"
    HYBRID = "hybrid"

    @property
    def includes_frontend_containers(self):
        return self is DockerDeploymentMode.FULL

    @property
    def checks_frontend_routes(self):
        return self in (DockerDeploymentMode.FULL, DockerDeploymentMode.HYBRID)

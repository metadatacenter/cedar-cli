from enum import Enum


class CedarMode(str, Enum):
    NATIVE = "native"
    HYBRID = "hybrid"
    DOCKER = "docker"


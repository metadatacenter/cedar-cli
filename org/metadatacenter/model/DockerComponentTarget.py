from enum import Enum


class DockerFrontendTarget(str, Enum):
    ALL = "all"
    MAIN = "main"
    OPENVIEW = "openview"
    MONITORING = "monitoring"
    BRIDGING = "bridging"
    CONTENT = "content"
    WORKSPACE = "workspace"
    DESIGNER = "designer"


class DockerMicroserviceTarget(str, Enum):
    ALL = "all"
    ARTIFACT = "artifact"
    BRIDGE = "bridge"
    GROUP = "group"
    IMPEX = "impex"
    MESSAGING = "messaging"
    MONITOR = "monitor"
    OPEN = "open"
    REPO = "repo"
    RESOURCE = "resource"
    SCHEMA = "schema"
    SUBMISSION = "submission"
    TERMINOLOGY = "terminology"
    USER = "user"
    VALUE_RECOMMENDER = "valuerecommender"
    WORKER = "worker"

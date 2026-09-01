from enum import Enum


class CedarProfile(str, Enum):
    """Which native environment a host runs.

    A workstation builds the frontends itself and reaches Keycloak over locally issued .orgx
    leaves; a staging or production host serves built payloads and verifies certificates. That
    difference is the whole of what separates one native host from another, so it is recorded
    once and the rest of the environment is derived.
    """

    DEVELOP = "develop"
    SERVER = "server"

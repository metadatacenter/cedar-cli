"""Load Nexus credentials independently of train or release orchestration."""
from org.metadatacenter.util.InvocationContext import invocation_environment
import os
from pathlib import Path
import xml.etree.ElementTree as ET

MAVEN_RELEASE_SERVER_ID = "bmir-nexus-releases"


class CredentialError(ValueError):
    """Credential configuration could not be read."""


def maven_settings_credentials(environment: dict) -> tuple[str, str] | None:
    """Read the release server without assuming Maven's optional XML namespace.

    An explicitly supplied environment is deliberately hermetic: if it has no HOME,
    do not fall through to the process user's settings and make tests or automation
    depend on an unrelated account.
    """
    home = environment.get("HOME")
    if not home:
        return None
    settings = Path(home).expanduser() / ".m2" / "settings.xml"
    if not settings.is_file():
        return None
    try:
        root = ET.parse(settings).getroot()
    except (OSError, ET.ParseError) as error:
        raise CredentialError(f"cannot read Maven settings {settings}: {error}") from error

    def local_name(element: ET.Element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    for server in root.iter():
        if local_name(server) != "server":
            continue
        values = {
            local_name(child): (child.text or "").strip()
            for child in server
        }
        if values.get("id") != MAVEN_RELEASE_SERVER_ID:
            continue
        username = values.get("username", "")
        password = values.get("password", "")
        if username and password:
            return username, password
        return None
    return None


def environment_with_nexus_credentials(environment=None) -> dict:
    """Prefer explicit credentials and fill only missing values from Maven settings."""
    values = dict(invocation_environment() if environment is None else environment)
    if values.get("BMIR_NEXUS_USERNAME") and values.get("BMIR_NEXUS_PASSWORD"):
        return values
    credentials = maven_settings_credentials(values)
    if credentials is not None:
        username, password = credentials
        if not values.get("BMIR_NEXUS_USERNAME"):
            values["BMIR_NEXUS_USERNAME"] = username
        if not values.get("BMIR_NEXUS_PASSWORD"):
            values["BMIR_NEXUS_PASSWORD"] = password
    return values


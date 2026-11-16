"""Whether the MCP servers validate with an artifact library at least as new as the platform's release.

The MCP servers under `$CEDAR_HOME/mcp` are built outside the platform's reactor and pin
cedar-artifact-library by a released version of their own choosing. A pin older than the platform's
last release validates against rules the deployed server no longer applies: with 2.9.31,
`validate_schema_artifact` calls valid a field that 2.9.35 and later refuse. The platform's last
release is the newest `release-<version>` tag in cedar-parent.
"""
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from lxml import etree

LIBRARY = ('org.metadatacenter', 'cedar-artifact-library')
POM = '{http://maven.apache.org/POM/4.0.0}'
RELEASE_TAG = re.compile(r'release-(\d+)\.(\d+)\.(\d+)')
VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)')


@dataclass(frozen=True)
class McpPin:
    server: str
    version: str


def last_release(home: Path) -> tuple[int, int, int] | None:
    """The newest platform release, read from cedar-parent's release tags."""
    result = subprocess.run(['git', '-C', str(home / 'cedar-parent'), 'tag', '--list', 'release-*'],
                            capture_output=True, text=True, check=False)
    releases = [tuple(int(part) for part in match.groups())
                for match in map(RELEASE_TAG.fullmatch, result.stdout.split()) if match]
    return max(releases) if releases else None


def pinned_version(pom: Path) -> str | None:
    """The cedar-artifact-library version a POM depends on, with a property reference resolved."""
    root = etree.parse(str(pom)).getroot()
    properties = {element.tag.removeprefix(POM): (element.text or '').strip()
                  for element in root.findall(f'{POM}properties/*') if isinstance(element.tag, str)}
    for dependency in root.iter(f'{POM}dependency'):
        group, artifact = (dependency.findtext(f'{POM}{name}', '').strip() for name in ('groupId', 'artifactId'))
        if (group, artifact) != LIBRARY:
            continue
        version = dependency.findtext(f'{POM}version', '').strip()
        reference = re.fullmatch(r'\$\{([^}]+)\}', version)
        return properties.get(reference.group(1)) if reference else version or None
    return None


def mcp_pins(home: Path) -> list[McpPin]:
    pins = []
    for pom in sorted((home / 'mcp').glob('*/pom.xml')):
        version = pinned_version(pom)
        if version is not None:
            pins.append(McpPin(pom.parent.name, version))
    return pins


def stale_pins(home: Path) -> list[str]:
    """One sentence per MCP server that pins an artifact library older than the platform's release."""
    release = last_release(home)
    pins = mcp_pins(home)
    if release is None:
        return ['cedar-parent has no release-<version> tag, so the MCP servers\' artifact library pins '
                'could not be compared with the platform'] if pins else []
    found = []
    for pin in pins:
        match = VERSION.match(pin.version)
        if match is None or tuple(int(part) for part in match.groups()) < release:
            found.append(f"mcp/{pin.server} pins cedar-artifact-library {pin.version}, older than the "
                         f"platform's release {'.'.join(map(str, release))}")
    return found

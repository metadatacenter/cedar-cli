"""Whether what a browser application serves still stands for the component sources beside it.

The browser applications consume each other as published npm packages. CEE, CED and the term
picker each publish a single-file custom-element bundle, and a host repository installs that
package, stages the bundle into the tree nginx serves, and creates the elements it defines. So a
component's source and the bundle its host serves are two different things, separated by a
publication and a pin, and a change to the source reaches the host only when both have moved.

Nothing compared them. A component could carry a committed, tested, reviewed change that no host
served, and every signal a developer looks at stayed green: the component's branch, its CI, its
host's CI, and the host's own suite, which exercises the staged bundle on the developer's disk
rather than the one a clean install resolves.

On 2026-09-16 that produced the case this module is built from. CED gained a field designer
element, `cedar-embeddable-field-designer`. The Template Designer's host was wired to create it
the same day. The Designer's pin was never advanced, so the package it locks is the snapshot from
two commits earlier, which defines no such element. The developer's machine worked, because a
locally built bundle was staged over the locked one; a clean install, and every server payload,
would have served a bundle without the element and failed at the point a person opened a field.

Three comparisons cover that, and none of them needs a judgement about versions:

- A pin names a source commit. Measuring it against the component's own head says how much
  committed work the host cannot see.
- A staged bundle carries bytes. Measuring them against the locked package says whether what is
  served is what a clean install would serve.
- A host names the elements it creates. Looking for each one in the locked bundles says whether
  the thing it asks for exists at all.

This module is the verdict, kept apart from the reading so it can be tested without a workspace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# cedarcli stamps a development snapshot as 2.0.16-dev.20260915.5652527d: the version it carries,
# the day it was built and the source commit it was built from. The commit is the only part a host
# can check against the component's own history.
DEV_VERSION = re.compile(r"^\d+\.\d+\.\d+-dev\.\d{8}\.(?P<commit>[0-9a-f]{7,40})$")

# A dependency may reach a scoped package through npm's alias form, which carries its own version:
#   "cedar-model-typescript-library": "npm:@org.metadatacenter/cedar-model-typescript-library@1.0.13"
ALIAS = re.compile(r"^npm:(?P<package>@[^/]+/[^@]+|[^@]+)@(?P<version>.+)$")

# The element names a host can create, and the only names worth looking for in a bundle.
ELEMENT_NAME = re.compile(r"\bcedar-embeddable-[a-z][a-z0-9-]*\b")

DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies")

# How many of the commits a pin cannot see to name in a report. Enough to recognise the change
# that matters, short enough that a host a month behind still prints one row.
SUBJECT_LIMIT = 4


class Surface(Enum):
    """Which of the three comparisons produced a finding."""

    PIN = "pin"
    BUNDLE = "bundle"
    ELEMENT = "element"


class ComponentState(Enum):
    """What the comparison says about the component the host consumes."""

    CURRENT = "current"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UNRESOLVED = "unresolved"
    OVERRIDDEN = "overridden"
    MISMATCHED = "mismatched"
    UNDEFINED = "undefined"
    UNDECLARED = "undeclared"


# What runs is not what the pin says, or is not there at all. Each of these is a defect in the
# workspace as it stands rather than a stage of ordinary work.
#
# An undeclared consumer belongs here rather than with the states below it, because it is not a
# stage of anything: publishing the component advances the pins its inventory names, so a host
# that pins it without being named is never advanced and never reported behind either. It reads
# as a coherent estate until somebody opens the surface that has rotted.
ALWAYS_FAIL = (
    ComponentState.MISMATCHED,
    ComponentState.UNDEFINED,
    ComponentState.DIVERGED,
    ComponentState.UNDECLARED,
)

# True of an estate mid-cycle. A host sits on the last published component for as long as it takes
# to publish the next one, and a local override is how a component is tried out before it is
# published. Failing on either by default would make the check something people learn to ignore,
# which is the outcome that leaves the defects above unread.
STRICT_FAIL = (ComponentState.BEHIND, ComponentState.OVERRIDDEN, ComponentState.UNRESOLVED)


@dataclass(frozen=True)
class ComponentFinding:
    host: str
    component: str
    surface: Surface
    state: ComponentState
    detail: str
    unseen: tuple[str, ...] = field(default=())

    @property
    def is_failure(self) -> bool:
        return self.state in ALWAYS_FAIL

    @property
    def is_strict_failure(self) -> bool:
        return self.state in ALWAYS_FAIL + STRICT_FAIL


def is_cedar_package(name: str) -> bool:
    """Whether this package name belongs to a repository in the workspace."""
    return unscoped(name).startswith("cedar-")


def unscoped(name: str) -> str:
    """A package name without its scope.

    A component publishes under a scope its source does not carry, and two components publish
    under different scopes, so the tail is the only name both sides agree on.
    """
    return name.rsplit("/", 1)[-1] if name else name


def dependency_pins(package: dict) -> dict[str, str]:
    """Every workspace package this one depends on, by package name, with the version asked for.

    The alias form is resolved to the package it reaches, because the key a host writes is a local
    label and says nothing about what npm installs.
    """
    pins: dict[str, str] = {}
    for section in DEPENDENCY_SECTIONS:
        declared = package.get(section)
        if not isinstance(declared, dict):
            continue
        for key, requested in declared.items():
            if not isinstance(key, str) or not isinstance(requested, str):
                continue
            alias = ALIAS.match(requested.strip())
            name, version = (alias.group("package"), alias.group("version")) if alias else (key, requested.strip())
            if is_cedar_package(name):
                pins[name] = version
    return pins


def pinned_commit(version: str) -> str | None:
    """The source commit a development version names, or None for a version that names none."""
    match = DEV_VERSION.match((version or "").strip())
    return match.group("commit") if match else None


def release_tag(version: str) -> str | None:
    """The tag a plain release version is published under, or None when the version is not one."""
    candidate = (version or "").strip()
    return f"release-{candidate}" if re.fullmatch(r"\d+\.\d+\.\d+", candidate) else None


def referenced_elements(source: str) -> set[str]:
    """The component element names a host's source mentions."""
    return set(ELEMENT_NAME.findall(source or ""))


def evaluate_pin(host: str,
                 component: str,
                 version: str,
                 pinned: str | None,
                 head: str | None,
                 unseen: tuple[str, ...],
                 reachable: bool) -> ComponentFinding:
    """The verdict for one host's pin on one component.

    An unreachable pin is a failure rather than an unknown. The pin names a commit, the component
    is in the workspace, and a commit its history does not hold means the artifact was built from
    something the source no longer accounts for: a purged snapshot, or a branch rewritten under it.
    """
    if pinned is None:
        return ComponentFinding(host, component, Surface.PIN, ComponentState.UNRESOLVED,
                                f"{version} names no source commit and no release tag")
    if head is None:
        return ComponentFinding(host, component, Surface.PIN, ComponentState.UNRESOLVED,
                                "the component's develop head could not be read")
    if not reachable:
        return ComponentFinding(host, component, Surface.PIN, ComponentState.DIVERGED,
                                f"{version} was built from {pinned[:8]}, which is not on develop")
    if unseen:
        return ComponentFinding(host, component, Surface.PIN, ComponentState.BEHIND,
                                f"{version} predates {_count(len(unseen))} on develop",
                                unseen[:SUBJECT_LIMIT])
    return ComponentFinding(host, component, Surface.PIN, ComponentState.CURRENT,
                            f"{version} was built from the develop head")


def evaluate_bundle(host: str,
                    component: str,
                    served_source: str,
                    served_sha: str | None,
                    locked_version: str | None,
                    locked_sha: str | None) -> ComponentFinding:
    """The verdict for one staged bundle against the package the host locks.

    A local override does not fail on its own. Staging a locally built bundle is how a component is
    tried in its host before it is published, and the staging script already refuses one when it is
    building a server payload. It is reported because it makes every other signal about this host
    describe bytes nobody else has.
    """
    if served_source != "package":
        return ComponentFinding(host, component, Surface.BUNDLE, ComponentState.OVERRIDDEN,
                                "a local build is staged over the locked package")
    if locked_sha is None:
        return ComponentFinding(host, component, Surface.BUNDLE, ComponentState.UNRESOLVED,
                                "the locked package is not installed")
    if served_sha != locked_sha:
        return ComponentFinding(host, component, Surface.BUNDLE, ComponentState.MISMATCHED,
                                f"served bundle is not the {locked_version} package it claims")
    return ComponentFinding(host, component, Surface.BUNDLE, ComponentState.CURRENT,
                            f"serving the locked {locked_version}")


def evaluate_element(host: str, element: str, defined_by: str | None) -> ComponentFinding:
    """The verdict for one element a host creates, against the bundles it locks."""
    if defined_by is None:
        return ComponentFinding(host, element, Surface.ELEMENT, ComponentState.UNDEFINED,
                                "no locked component bundle defines this element")
    return ComponentFinding(host, element, Surface.ELEMENT, ComponentState.CURRENT,
                            f"defined by {defined_by}")


def _count(commits: int) -> str:
    return "1 commit" if commits == 1 else f"{commits} commits"

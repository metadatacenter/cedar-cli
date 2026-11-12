"""The file edits a release makes, as pure functions of the text they change."""
from __future__ import annotations

import json
import re

from org.metadatacenter.npm_release_support.definitions import MODEL_PUBLIC_NAME
from org.metadatacenter.npm_release_support.errors import NpmReleaseError

_ENTRY_HEADING = re.compile(r"^## \[", re.MULTILINE)
_LOAD_TRACE = re.compile(r"(INNER_VERSION\s*=\s*')([^']*)(')")


def changelog_entry(version: str, date: str, model_version: str | None, notes: str) -> str:
    parts = [f"## [{version}] - {date}"]
    if model_version:
        parts.append(f"Aligns with `{MODEL_PUBLIC_NAME}@{model_version}`.")
    parts.append(notes.strip("\n"))
    return "\n\n".join(parts) + "\n\n"


def insert_changelog_entry(text: str, version: str, entry: str) -> str:
    """Add one dated entry above the newest released one, and change nothing else.

    The platform release proves a public CEE is a promotion of its train's by cutting this one entry
    out again and comparing what remains byte for byte. Notes under `## [Unreleased]` therefore stay
    where they are, and the entry goes below that section rather than replacing it.
    """
    if re.search(rf"^## \[{re.escape(version)}\]", text, re.MULTILINE):
        raise NpmReleaseError(f"the changelog already has an entry for {version}")
    headings = list(_ENTRY_HEADING.finditer(text))
    if not headings:
        raise NpmReleaseError("the changelog has no '## [' entry to place the release above")
    index = 0
    if text[headings[0].start():].startswith("## [Unreleased]"):
        if len(headings) < 2:
            separator = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
            return text + separator + entry.rstrip("\n") + "\n"
        index = 1
    position = headings[index].start()
    return text[:position] + entry + text[position:]


def stamp_load_trace(text: str, stamp: str) -> str:
    matches = list(_LOAD_TRACE.finditer(text))
    if len(matches) != 1:
        raise NpmReleaseError(f"expected one INNER_VERSION assignment, found {len(matches)}")
    match = matches[0]
    return text[:match.start(2)] + stamp + text[match.end(2):]


def published_manifest_name(text: str, name: str) -> str:
    """Rename the model's published manifest, which names its channel by scope."""
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as error:
        raise NpmReleaseError(f"the published manifest is not JSON: {error}") from error
    if not isinstance(manifest.get("name"), str):
        raise NpmReleaseError("the published manifest has no name")
    old = json.dumps(manifest["name"])
    pattern = re.compile(r'("name"\s*:\s*)' + re.escape(old))
    if len(pattern.findall(text)) != 1:
        raise NpmReleaseError("the published manifest's name is not uniquely placed")
    return pattern.sub(lambda match: match.group(1) + json.dumps(name), text, count=1)


def dependency_spec(public_model_version: str | None, development_model_version: str | None) -> str:
    if public_model_version:
        return f"{MODEL_PUBLIC_NAME}@{public_model_version}"
    if development_model_version:
        return (f"{MODEL_PUBLIC_NAME}@npm:@org.metadatacenter/{MODEL_PUBLIC_NAME}"
                f"@{development_model_version}")
    raise NpmReleaseError("no model version to pin")


def development_model_pin(manifest: dict) -> str | None:
    """The scoped model version a development manifest pins, or None for a public pin."""
    value = (manifest.get("dependencies") or {}).get(MODEL_PUBLIC_NAME)
    if not isinstance(value, str):
        return None
    prefix = f"npm:@org.metadatacenter/{MODEL_PUBLIC_NAME}@"
    return value[len(prefix):] if value.startswith(prefix) else None

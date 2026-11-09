"""The local CI registry: which workflow jobs local CI answers for, and how.

The registry lives beside the train configuration, in `cedar-development/ops/local-ci.json`. It
never restates a command a workflow runs. For each job it covers it records only what must differ
on this machine, with a reason for each difference: a step skipped because it publishes or installs
Linux packages, a step substituted because its port collides with the local stack, extra
environment, what the job requires, and which jobs may not run at the same time. A job the registry
does not name, or names as uncovered, is one GitHub still answers for.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from org.metadatacenter.local_ci.errors import LocalCIError

REGISTRY = Path("cedar-development") / "ops" / "local-ci.json"
SCHEMA_VERSION = 1
DEFAULT_STATUS_CONTEXT = "cedar/local-ci"


@dataclass(frozen=True)
class JobConfig:
    """What local CI does differently for one job, merged from its profiles."""

    skip: dict = field(default_factory=dict)
    substitute: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    requires: tuple = ()
    exclusive: str | None = None
    uncovered: str | None = None
    omit: str | None = None
    cleanup: str | None = None
    legs: dict = field(default_factory=dict)
    called: dict = field(default_factory=dict)

    def for_called(self, job_id: str) -> "JobConfig":
        """The configuration of one job of a reusable workflow this job calls."""
        nested = self.called.get(job_id)
        if nested is None:
            return JobConfig(env=dict(self.env), requires=self.requires, exclusive=self.exclusive)
        return nested

    def leg_uncovered(self, matrix: dict) -> str | None:
        """The reason a matrix leg is uncovered, when the registry singles it out."""
        for entry in self.legs.get("uncovered", []):
            selector = entry.get("matrix", {})
            if all(str(matrix.get(key)) == str(value) for key, value in selector.items()):
                return entry.get("reason", "this matrix leg is not covered locally")
        return None


def _merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if key in ("skip", "substitute", "env", "called", "legs") and isinstance(value, dict):
            merged[key] = {**merged.get(key, {}), **value}
        elif key == "requires":
            merged[key] = sorted(set(merged.get(key, [])) | set(value))
        else:
            merged[key] = value
    return merged


@dataclass(frozen=True)
class Registry:
    document: dict
    path: Path | None = None

    @property
    def status_context(self) -> str:
        return str(self.document.get("statusContext") or DEFAULT_STATUS_CONTEXT)

    def repositories(self) -> list[str]:
        return sorted(self.document.get("repositories", {}))

    def entry(self, repository: str) -> dict | None:
        return self.document.get("repositories", {}).get(repository)

    def digest(self, repository: str) -> str:
        """A fingerprint of everything the registry says about one repository.

        A record made under another fingerprint answers for nothing: the coverage it was made under
        is not the coverage the registry now states.
        """
        entry = self.entry(repository) or {}
        profiles = self.document.get("profiles", {})
        used = sorted({name for job in entry.get("jobs", {}).values()
                       for name in _profile_names(job)})
        payload = {"entry": entry, "profiles": {name: profiles.get(name) for name in used},
                   "schemaVersion": self.document.get("schemaVersion")}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def job(self, repository: str, key: str) -> JobConfig | None:
        entry = self.entry(repository)
        if entry is None:
            return None
        raw = entry.get("jobs", {}).get(key)
        if raw is None:
            return None
        return self._config(raw, f"{repository} {key}")

    def _config(self, raw: dict, label: str) -> JobConfig:
        merged: dict = {}
        profiles = self.document.get("profiles", {})
        for name in _profile_names(raw):
            if name not in profiles:
                raise LocalCIError(f"{label} names an unknown local CI profile {name!r}")
            merged = _merge(merged, profiles[name])
        merged = _merge(merged, {key: value for key, value in raw.items() if key != "profile"})
        for step, reason in merged.get("skip", {}).items():
            if not isinstance(reason, str) or not reason.strip():
                raise LocalCIError(f"{label} skips {step!r} without a reason")
        for step, replacement in merged.get("substitute", {}).items():
            if not isinstance(replacement, dict) or not replacement.get("run") or not replacement.get("reason"):
                raise LocalCIError(f"{label} substitutes {step!r} without a run and a reason")
        called = {job_id: self._config(nested, f"{label} -> {job_id}")
                  for job_id, nested in merged.get("called", {}).items()}
        return JobConfig(
            skip=dict(merged.get("skip", {})),
            substitute=dict(merged.get("substitute", {})),
            env={key: str(value) for key, value in merged.get("env", {}).items()},
            requires=tuple(merged.get("requires", [])),
            exclusive=merged.get("exclusive"),
            uncovered=merged.get("uncovered"),
            omit=merged.get("omit"),
            cleanup=merged.get("cleanup"),
            legs=dict(merged.get("legs", {})),
            called=called,
        )


def _profile_names(raw: dict) -> list[str]:
    value = raw.get("profile")
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def load_registry(cedar_home) -> Registry:
    path = Path(cedar_home) / REGISTRY
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LocalCIError(f"the local CI registry {path} does not exist") from error
    except json.JSONDecodeError as error:
        raise LocalCIError(f"the local CI registry {path} is not JSON: {error}") from error
    if document.get("schemaVersion") != SCHEMA_VERSION:
        raise LocalCIError(f"{path} has schema version {document.get('schemaVersion')!r}, "
                           f"expected {SCHEMA_VERSION}")
    return Registry(document, path)

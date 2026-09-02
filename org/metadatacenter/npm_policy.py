"""Source-bound npm lifecycle-script and user-configuration policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class NpmConfigFinding:
    severity: str
    keys: tuple[str, ...]
    message: str
    remedy: str


def _policy_versions(key: str, package_name: str) -> set[str] | None:
    prefix = package_name + "@"
    if not key.startswith(prefix):
        return None
    versions = {item.strip() for item in key[len(prefix):].split("||")}
    if not versions or "" in versions:
        return None
    return versions


def unreviewed_install_scripts(package: dict, lock: dict, identity: str) -> list[str]:
    """Return exact locked packages whose lifecycle scripts have no explicit verdict."""
    if not isinstance(package, dict):
        raise ValueError(f"{identity} package.json is not an object")
    if not isinstance(lock, dict):
        raise ValueError(f"{identity} package-lock.json is not an object")
    policy = package.get("allowScripts")
    if not isinstance(policy, dict):
        raise ValueError(f"{identity} package.json has no allowScripts policy")
    for key, decision in policy.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(decision, bool):
            raise ValueError(
                f"{identity} allowScripts must map non-empty package/version keys to booleans"
            )

    locked = lock.get("packages")
    if not isinstance(locked, dict):
        raise ValueError(f"{identity} package-lock.json has no packages inventory")
    pending = []
    for installed_path, record in locked.items():
        if (
            not installed_path
            or not isinstance(record, dict)
            or not record.get("hasInstallScript")
        ):
            continue
        name = installed_path.rsplit("node_modules/", 1)[-1]
        version = record.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError(f"{identity} has an install-script dependency with no identity")
        # A bare false is an explicit deny for every locked version. A versioned true or
        # false is also reviewed: strict-allow-scripts executes only true entries, while a
        # false entry records the deliberate decision not to execute it.
        reviewed = policy.get(name) is False
        if not reviewed:
            reviewed = any(
                version in versions
                for key in policy
                if (versions := _policy_versions(key, name)) is not None
            )
        if not reviewed:
            pending.append(f"{name}@{version}")
    return sorted(set(pending))


_NPMRC_KEY_RE = re.compile(r"^\s*([^#;\s][^=]*?)\s*=")


def npm_user_config_findings(path: Path) -> list[NpmConfigFinding]:
    """Inspect npmrc key names only; values may contain publication credentials."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise ValueError(f"cannot read npm user configuration {path}: {error}") from error
    keys = set()
    for line in lines:
        match = _NPMRC_KEY_RE.match(line)
        if match:
            keys.add(match.group(1).strip().lower())

    blockers = tuple(sorted(keys & {"always-auth"}))
    advisories = tuple(sorted(
        key for key in keys
        if key == "email" or key.startswith("--init.author.")
    ))
    findings = []
    if blockers:
        findings.append(NpmConfigFinding(
            "fail",
            blockers,
            "npm no longer recognizes authentication setting(s): " + ", ".join(blockers),
            f"remove {', '.join(blockers)} from {path}; use registry-scoped credentials instead",
        ))
    if advisories:
        findings.append(NpmConfigFinding(
            "warn",
            advisories,
            "npm no longer recognizes user setting(s): " + ", ".join(advisories),
            f"remove or correct {', '.join(advisories)} in {path}",
        ))
    return findings

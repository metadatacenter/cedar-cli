"""Bounded exact-commit GitHub Actions probing shared by trains and releases."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
import time
from typing import Callable


GREEN_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})
TRANSIENT_HTTP_CODES = frozenset({502, 503, 504})


@dataclass(frozen=True)
class GithubCIProbe:
    repository: str
    revision: str
    runs: tuple[dict, ...]
    attempts: int


class GithubCIProbeError(RuntimeError):
    pass


def _transient_error(detail: str) -> bool:
    lowered = detail.lower()
    if any(re.search(rf"\b(?:http\s+)?{code}\b", lowered)
           for code in TRANSIENT_HTTP_CODES):
        return True
    return any(fragment in lowered for fragment in (
        "timed out", "timeout", "connection reset", "connection refused",
        "temporary failure", "could not resolve host", "unexpected eof",
    ))


def probe_exact_commit(
    repository: str,
    revision: str,
    *,
    runner=None,
    sleeper: Callable[[float], None] = time.sleep,
    delays: tuple[float, ...] = (2, 5, 10),
    reporter: Callable[[str], None] | None = None,
) -> GithubCIProbe:
    """Read Actions runs, retrying only transient transport and indexing absence."""
    runner = runner or subprocess.run
    total = len(delays) + 1
    route = (
        f"repos/metadatacenter/{repository}/actions/runs"
        f"?head_sha={revision}&per_page=20"
    )
    for index in range(total):
        attempt = index + 1
        try:
            result = runner(
                ["gh", "api", route],
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            detail = str(error)
            if index < len(delays) and _transient_error(detail):
                delay = delays[index]
                if reporter:
                    reporter(
                        f"{repository} {revision[:8]} CI probe attempt {attempt}/{total} "
                        f"failed transiently ({detail}); retrying in {delay:g}s"
                    )
                sleeper(delay)
                continue
            raise GithubCIProbeError(
                f"{repository} {revision[:8]} CI state is unreadable: {detail}"
            ) from error
        detail = (result.stderr or result.stdout or "").strip()
        if result.returncode:
            if index < len(delays) and _transient_error(detail):
                delay = delays[index]
                if reporter:
                    reporter(
                        f"{repository} {revision[:8]} CI probe attempt {attempt}/{total} "
                        f"failed transiently ({detail.splitlines()[-1] if detail else result.returncode}); "
                        f"retrying in {delay:g}s"
                    )
                sleeper(delay)
                continue
            raise GithubCIProbeError(
                f"{repository} {revision[:8]} CI state is unreadable: "
                f"{detail.splitlines()[-1] if detail else f'exit {result.returncode}'}"
            )
        try:
            payload = json.loads(result.stdout or "")
        except json.JSONDecodeError as error:
            raise GithubCIProbeError(
                f"{repository} {revision[:8]} CI state is malformed JSON"
            ) from error
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            raise GithubCIProbeError(
                f"{repository} {revision[:8]} CI response has no workflow_runs list"
            )
        if runs:
            return GithubCIProbe(repository, revision, tuple(runs), attempt)
        if index < len(delays):
            delay = delays[index]
            if reporter:
                reporter(
                    f"{repository} {revision[:8]} CI is not indexed on attempt "
                    f"{attempt}/{total}; retrying in {delay:g}s"
                )
            sleeper(delay)
            continue
        return GithubCIProbe(repository, revision, (), attempt)
    raise AssertionError("unreachable GitHub CI probe state")


def latest_runs_by_name(runs: tuple[dict, ...] | list[dict]) -> dict[str, dict]:
    latest = {}
    for record in runs:
        if not isinstance(record, dict):
            continue
        name = record.get("name") or str(record.get("workflow_id") or "CI")
        latest.setdefault(name, record)
    return latest


def run_url(record: dict) -> str:
    value = record.get("html_url")
    if isinstance(value, str) and value:
        return value
    run_id = record.get("id")
    repository = record.get("repository", {}).get("full_name") \
        if isinstance(record.get("repository"), dict) else None
    if run_id and repository:
        return f"https://github.com/{repository}/actions/runs/{run_id}"
    return ""

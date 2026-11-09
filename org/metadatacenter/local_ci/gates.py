"""What a gate asks about CI: local CI's answer first, then GitHub's, told apart from GitHub's faults.

Every gate that used to wait for a green GitHub Actions run at a commit now accepts a passing local
CI run of the same commit instead. When it does read GitHub, it no longer takes GitHub's own
failures for the code's: a run that concluded failure without one of its jobs failing never ran the
code, and a gate reruns it or waits for it rather than reporting a red commit.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from org.metadatacenter.local_ci.evidence import LocalVerdict, verdict
from org.metadatacenter.local_ci.errors import LocalCIError

FAILED_JOB_CONCLUSIONS = frozenset({"failure", "timed_out"})


def local_answer(cedar_home, repository: str, revision: str, *, environment=None,
                 runner=subprocess.run, read_status: bool = True) -> LocalVerdict:
    """Local CI's answer for one commit, or 'none' whenever it cannot give one."""
    if not cedar_home:
        return LocalVerdict("none", "CEDAR_HOME is not set")
    from org.metadatacenter.local_ci.registry import load_registry
    try:
        registry = load_registry(cedar_home)
    except LocalCIError as error:
        return LocalVerdict("none", str(error))
    if registry.entry(repository) is None:
        return LocalVerdict("none", f"local CI does not cover {repository}")
    try:
        return verdict(cedar_home, repository, revision, registry=registry, runner=runner,
                       environment=environment, read_status=read_status)
    except (LocalCIError, OSError) as error:
        return LocalVerdict("none", str(error))


def github_failed_to_start(repository: str, run: dict, *, runner=subprocess.run,
                           environment=None) -> bool:
    """Whether a red run is GitHub's failure rather than the code's.

    GitHub reports a run that could not start as `startup_failure`, or, when its job graph failed to
    materialise, as `failure` with no failed job: during the Actions outage of 2026-11-09 a run
    concluded failure with every job skipped or absent and an "Internal server error" annotation.
    """
    if run.get("status") != "completed":
        return False
    if run.get("conclusion") == "startup_failure":
        return True
    if run.get("conclusion") != "failure" or not run.get("id"):
        return False
    try:
        result = runner(["gh", "api", f"repos/metadatacenter/{repository}/actions/runs/{run['id']}/jobs"
                         "?per_page=100"], text=True, capture_output=True, check=False, env=environment)
        if getattr(result, "returncode", 1):
            return False
        payload = json.loads(getattr(result, "stdout", "") or "{}")
    except (OSError, ValueError, TypeError):
        # Unreadable jobs say nothing about the run, so the run keeps the verdict GitHub gave it.
        return False
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return False
    return not any(isinstance(job, dict) and job.get("conclusion") in FAILED_JOB_CONCLUSIONS
                   for job in jobs)


def rerun(repository: str, run_id, *, runner=subprocess.run, environment=None) -> str | None:
    """Ask GitHub to run a run again; the error, if it refused."""
    try:
        result = runner(["gh", "run", "rerun", str(run_id), "--repo", f"metadatacenter/{repository}"],
                        text=True, capture_output=True, check=False, env=environment)
    except OSError as error:
        return str(error)
    if getattr(result, "returncode", 1) == 0:
        return None
    return (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "refused").strip()


def cedar_home_from(environment=None):
    from org.metadatacenter.util.InvocationContext import invocation_environment
    from org.metadatacenter.util.Util import Util
    values = invocation_environment() if environment is None else environment
    home = values.get("CEDAR_HOME") or Util.cedar_home
    return Path(home) if home else None

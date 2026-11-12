"""The record of a local CI run, and the verdict a gate reads from it.

A run is evidence about one commit. Its record names the commit and its tree, every other commit the
jobs checked out, the toolchain, the registry fingerprint the coverage was decided under, and each
job's result and log. The record is kept on this machine under `~/.cedar/local-ci/evidence`, and the
run also posts a commit status, `cedar/local-ci` by default, so that a pull request and a gate on
another machine see the same answer.

A gate asks one question of this module: is there a passing local CI run for exactly this commit,
made under the registry's present coverage? A passing GitHub Actions run answers the same question,
and either answer suffices.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from org.metadatacenter.github_ci import newest_commit_status
from org.metadatacenter.local_ci.errors import LocalCIError

SCHEMA_VERSION = 1


def root(environment=None) -> Path:
    environment = os.environ if environment is None else environment
    configured = environment.get("CEDAR_LOCAL_CI_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".cedar" / "local-ci"


def evidence_path(repository: str, revision: str, environment=None) -> Path:
    return root(environment) / "evidence" / repository / f"{revision}.json"


def write_record(record: dict, environment=None) -> Path:
    path = evidence_path(record["repository"], record["revision"], environment)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def read_record(repository: str, revision: str, environment=None) -> dict | None:
    path = evidence_path(repository, revision, environment)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise LocalCIError(f"the local CI record {path} is not JSON: {error}") from error


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def host() -> str:
    return socket.gethostname().split(".")[0]


def status_description(record: dict) -> str:
    jobs = record.get("jobs", [])
    minutes = max(1, round(sum(job.get("seconds", 0) for job in jobs) / 60))
    verdict = "passed" if record.get("conclusion") == "success" else "failed"
    # The public description names no machine; the record kept on the machine does.
    text = (f"{len(jobs)} job(s) {verdict} locally in {minutes} min; "
            f"registry {record.get('registrySha256', '')[:8]}")
    return text[:140]


def post_status(record: dict, context: str, runner=subprocess.run, environment=None) -> dict:
    """Publish the verdict on the commit, where pull requests and other machines can read it."""
    state = "success" if record.get("conclusion") == "success" else "failure"
    command = ["gh", "api", "--method", "POST",
               f"repos/metadatacenter/{record['repository']}/statuses/{record['revision']}",
               "-f", f"state={state}", "-f", f"context={context}",
               "-f", f"description={status_description(record)}"]
    result = runner(command, text=True, capture_output=True, check=False, env=environment)
    if result.returncode:
        raise LocalCIError(f"could not post the {context} status on {record['repository']} "
                           f"{record['revision'][:8]}: {(result.stderr or result.stdout).strip()}")
    try:
        posted = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        posted = {}
    return {"context": context, "state": state, "url": posted.get("url", ""),
            "postedAt": dt.datetime.now(dt.timezone.utc).isoformat()}


@dataclass(frozen=True)
class LocalVerdict:
    """What local CI says about one commit: 'green', 'red' or 'none'."""

    state: str
    detail: str
    source: str = ""

    @property
    def green(self) -> bool:
        return self.state == "green"


def _tree(cedar_home, repository: str, revision: str) -> str | None:
    result = subprocess.run(["git", "-C", str(Path(cedar_home) / repository), "rev-parse",
                             f"{revision}^{{tree}}"], text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def ledger_verdict(cedar_home, repository: str, revision: str, registry_digest: str | None,
                   environment=None) -> LocalVerdict:
    record = read_record(repository, revision, environment)
    if record is None:
        return LocalVerdict("none", f"no local CI run for {repository} {revision[:8]}")
    if record.get("schemaVersion") != SCHEMA_VERSION or record.get("revision") != revision:
        return LocalVerdict("none", f"the local CI record for {revision[:8]} has another shape")
    if registry_digest is not None and record.get("registrySha256") != registry_digest:
        return LocalVerdict("none", f"local CI ran {revision[:8]} under an earlier registry entry; "
                                    "run it again")
    tree = _tree(cedar_home, repository, revision)
    if tree is not None and record.get("tree") != tree:
        return LocalVerdict("none", f"the local CI record for {revision[:8]} names another tree")
    when = record.get("finishedAt", "")[:16].replace("T", " ")
    if record.get("conclusion") == "success" and record.get("covered"):
        return LocalVerdict("green", f"local CI passed on {record.get('host')} at {when} UTC", "ledger")
    if record.get("conclusion") == "success":
        return LocalVerdict("none", "local CI passed but did not cover every job", "ledger")
    failed = [job.get("leg") for job in record.get("jobs", []) if job.get("conclusion") == "failure"]
    return LocalVerdict("red", f"local CI failed at {when} UTC: {', '.join(failed) or 'see the log'}",
                        "ledger")


def status_verdict(repository: str, revision: str, context: str, runner=subprocess.run,
                   environment=None) -> LocalVerdict:
    """The newest `cedar/local-ci` status GitHub holds for the commit, for another machine's runs."""
    newest, reason = newest_commit_status(repository, revision, context, runner=runner,
                                          environment=environment)
    if newest is None:
        return LocalVerdict("none", reason)
    if newest.get("state") == "success":
        return LocalVerdict("green", f"{context}: {newest.get('description', '')}", "status")
    if newest.get("state") in ("failure", "error"):
        return LocalVerdict("red", f"{context}: {newest.get('description', '')}", "status")
    return LocalVerdict("none", f"{context} is {newest.get('state')}", "status")


def verdict(cedar_home, repository: str, revision: str, *, registry=None, runner=subprocess.run,
            environment=None, read_status: bool = True) -> LocalVerdict:
    """Local CI's answer for one commit, from this machine's ledger or else the commit status.

    The ledger answers only under the registry's present entry for the repository, and only for
    a run that covered every job. A status was posted under the same rules by whichever machine ran.
    """
    digest = None
    context = "cedar/local-ci"
    if registry is not None:
        digest = registry.digest(repository)
        context = registry.status_context
        if registry.entry(repository) is None:
            return LocalVerdict("none", f"the local CI registry does not cover {repository}")
    answer = ledger_verdict(cedar_home, repository, revision, digest, environment)
    if answer.state != "none" or not read_status:
        return answer
    return status_verdict(repository, revision, context, runner, environment)


def publish_record(cedar_home, repository: str, revision: str, registry, runner=subprocess.run,
                   environment=None) -> dict | None:
    """Post the status of this machine's passing record, unless GitHub already holds a passing one.

    A record is evidence on this machine alone, and a run made with `--no-status` posted nothing
    beside it. A gate elsewhere, the train's runner among them, reads only the status. Returns the
    posted status, or None when the record does not pass or its status is already there.
    """
    answer = ledger_verdict(cedar_home, repository, revision, registry.digest(repository), environment)
    if not answer.green:
        return None
    if status_verdict(repository, revision, registry.status_context, runner, environment).green:
        return None
    return post_status(read_record(repository, revision, environment), registry.status_context,
                       runner, environment)

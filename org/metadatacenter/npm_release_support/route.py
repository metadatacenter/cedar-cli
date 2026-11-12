"""The stages of an npm release, each proving its outcome before the ledger records it.

A package passes through the stages below in order. Each stage is safe to run again: it first
asks whether its outcome already holds, which is what lets `resume` continue a run interrupted
anywhere, including between a remote step and the record of it. A stage that cannot establish
its outcome stops the run rather than assume it.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import re
import shutil

from org.metadatacenter.github_ci import GREEN_CONCLUSIONS, GithubCIProbeError, probe_exact_commit, run_url
from org.metadatacenter.npm_package import NpmPackageError, inspect_tarball, pack_and_inspect
from org.metadatacenter.npm_release_support.definitions import (
    MODEL_DEV_NAME,
    MODEL_PUBLIC_NAME,
    NEXUS_REGISTRY,
    NPM_ACCOUNT,
    PACKAGES,
    PUBLIC_REGISTRY,
    NpmPackage,
)
from org.metadatacenter.npm_release_support.edits import (
    changelog_entry,
    dependency_spec,
    insert_changelog_entry,
    published_manifest_name,
    stamp_load_trace,
)
from org.metadatacenter.npm_release_support.errors import NpmReleaseError, NpmReleaseWait
from org.metadatacenter.npm_release_support.state import NpmReleaseState, now
from org.metadatacenter.npm_release_support.tools import Tools, sha256

PACKAGE_STAGES = (
    "prepare",
    "gate",
    "commit",
    "push",
    "pull-request",
    "checks",
    "merge",
    "build-main",
    "publish",
    "verify",
    "tag",
    "notes",
    "develop",
    "development-checks",
)
# Nothing a package's stages up to here have done is visible on npmjs or main, so the run may
# still be abandoned. A merged pull request already changed main.
ABANDONABLE_BEFORE = "merge"
RUN_STAGES = ("baselines",)
CI_POLL_SECONDS = 30
CI_TIMEOUT_SECONDS = 3 * 3600
REGISTRY_DELAYS = (5, 10, 15, 20, 30, 30, 45, 60, 60, 60, 90, 90)
PR_CHECK_GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}
REGISTRY_LAG = re.compile(r"ETARGET|E404|No matching version|notarget", re.IGNORECASE)
PUBLISH_CONFLICT = re.compile(
    r"EPUBLISHCONFLICT|cannot publish over|You cannot publish over the previously published versions",
    re.IGNORECASE)


def nexus_tarball_url(name: str, version: str) -> str:
    unscoped = name.split("/")[-1]
    return f"{NEXUS_REGISTRY}{name}/-/{unscoped}-{version}.tgz"


class NpmReleaseRoute:
    def __init__(self, cedar_home: Path, tools: Tools, state: NpmReleaseState, *, reporter=print,
                 ci_poll_seconds: float = CI_POLL_SECONDS, ci_timeout_seconds: float = CI_TIMEOUT_SECONDS,
                 registry_delays: tuple[float, ...] = REGISTRY_DELAYS, ci_probe=probe_exact_commit,
                 baseline_refresher=None, platform_release_active=None, cli: tuple[str, ...] | None = None,
                 local_ci=None):
        self.home = Path(cedar_home)
        self.tools = tools
        self.state = state
        self.report = reporter
        self.ci_poll_seconds = ci_poll_seconds
        self.ci_timeout_seconds = ci_timeout_seconds
        self.registry_delays = registry_delays
        self.ci_probe = ci_probe
        self.baseline_refresher = baseline_refresher
        self.platform_release_active = platform_release_active
        self.cli = cli or ("bash", str(self.home / "cedar-cli" / "cli.sh"))
        self.local_ci = local_ci if local_ci is not None else self._run_local_ci

    # The driver ------------------------------------------------------------------------------

    def drive(self, run: dict) -> dict:
        """Carry every package, then the run's own closing stage, as far as each can be proved."""
        run["failure"] = None
        run["waiting"] = None
        self.state.save(run)
        for record in run["packages"]:
            definition = PACKAGES[record["package"]]
            for stage in PACKAGE_STAGES:
                if stage in record["completed"]:
                    continue
                self._run_stage(run, record, stage, lambda: getattr(self, "_" + stage.replace("-", "_"))(
                    definition, record, run))
        for stage in RUN_STAGES:
            if stage in run["completed"]:
                continue
            self._run_stage(run, run, stage, lambda: self._baselines(run))
        self.state.conclude(run, "released")
        return run

    def _run_stage(self, run: dict, record: dict, stage: str, action) -> None:
        label = record.get("package", "run")
        self.report(f"{label}: {stage}")
        record["stage"] = stage
        record["stageStartedAt"] = now()
        self.state.save(run)
        try:
            evidence = action()
        except NpmReleaseWait as wait:
            run["waiting"] = {"package": label, "stage": stage, "detail": str(wait), "at": now()}
            self.state.save(run)
            raise
        except NpmReleaseError as error:
            run["failure"] = {"package": label, "stage": stage, "detail": str(error), "at": now()}
            self.state.save(run)
            raise
        record.setdefault("evidence", {})[stage] = {**(evidence or {}), "completedAt": now()}
        record["completed"].append(stage)
        record["stage"] = None
        self.state.save(run)

    # Helpers ---------------------------------------------------------------------------------

    def repository(self, definition: NpmPackage) -> Path:
        return self.home / definition.repository

    def _attempt(self, run: dict, definition: NpmPackage) -> Path:
        return self.state.attempt_directory(run["id"], definition.id)

    def _dirty(self, repository: Path) -> list[str]:
        """Tracked and untracked paths that differ from HEAD, ignoring what Git ignores."""
        entries = self.tools.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], repository).stdout.split("\0")
        paths, index = set(), 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            paths.add(entry[3:])
            if entry[0] in "RC":
                index += 1  # A rename or copy is followed by the path it came from.
        return sorted(paths)

    def _allowed_paths(self, definition: NpmPackage) -> set[str]:
        paths = {"package.json", "package-lock.json"}
        for directory in definition.model_manifests:
            prefix = "" if directory == "." else f"{directory}/"
            paths |= {f"{prefix}package.json", f"{prefix}package-lock.json"}
        for path in (definition.changelog, definition.load_trace, definition.published_manifest):
            if path:
                paths.add(path)
        return paths

    def _tree_of(self, repository: Path, paths: list[str]) -> str:
        if paths:
            self.tools.git(repository, "add", "--", *paths)
        return self.tools.git(repository, "write-tree")

    def _logs(self, run: dict, definition: NpmPackage, name: str) -> Path:
        return self._attempt(run, definition) / "logs" / f"{name}.log"

    def _remote_ref(self, repository: Path, ref: str) -> str | None:
        text = self.tools.git(repository, "ls-remote", "origin", ref)
        rows = [line.split("\t") for line in text.splitlines() if line.strip()]
        values = {name: sha for sha, name in rows}
        peeled = values.get(f"{ref}^{{}}")
        return peeled or values.get(ref)

    def _install_model(self, definition: NpmPackage, spec: str, log: Path) -> None:
        """Pin the model exactly in every graph that names it, retrying a registry still catching up.

        npmjs serves a version a minute or two after its publish returns, so a release of the model
        followed at once by a consumer finds `ETARGET` or `E404` for that long.
        """
        repository = self.repository(definition)
        for directory in definition.model_manifests:
            cwd = repository if directory == "." else repository / directory
            for attempt, delay in enumerate((*self.registry_delays, None), start=1):
                offset = log.stat().st_size if log.exists() else 0
                try:
                    self.tools.logged(["npm", "install", "--save-exact", spec], cwd, log)
                    break
                except NpmReleaseError as error:
                    with log.open("rb") as stream:
                        stream.seek(offset)
                        output = stream.read().decode("utf-8", errors="replace")
                    if delay is None or not REGISTRY_LAG.search(output):
                        raise NpmReleaseError(
                            f"cannot install {spec} in {cwd} after {attempt} attempt(s): {error}") from error
                    self.report(f"  {spec} is not installable yet in {directory}; retrying in {delay:g}s")
                    self.tools.sleep(delay)

    def _model_resolutions(self, definition: NpmPackage) -> list[dict]:
        repository = self.repository(definition)
        resolutions = []
        for directory in definition.model_manifests:
            prefix = repository if directory == "." else repository / directory
            lock = json.loads((prefix / "package-lock.json").read_text())
            entry = (lock.get("packages") or {}).get(f"node_modules/{MODEL_PUBLIC_NAME}") or {}
            resolutions.append({
                "directory": directory,
                "version": entry.get("version"),
                "resolved": entry.get("resolved"),
                "integrity": entry.get("integrity"),
                "pin": (json.loads((prefix / "package.json").read_text()).get("dependencies") or {})
                .get(MODEL_PUBLIC_NAME),
            })
        return resolutions

    def _gh_command(self, command, **_):
        """A gh command, in the shape the local CI gate helpers expect, through the route's tools."""
        import subprocess as _subprocess
        result = self.tools.gh(*command[1:], check=False)
        return _subprocess.CompletedProcess(command, result.returncode, result.stdout, result.stderr)

    def _run_local_ci(self, repository: str, commit: str):
        """Run the repository's CI here at `commit`; None when local CI does not cover it."""
        from org.metadatacenter.local_ci.coverage import repository_coverage
        from org.metadatacenter.local_ci.errors import LocalCIError
        from org.metadatacenter.local_ci.registry import load_registry
        from org.metadatacenter.local_ci.run import run as run_local_ci
        try:
            registry = load_registry(self.home)
        except LocalCIError:
            return None
        if registry.entry(repository) is None:
            return None
        if not repository_coverage(self.home, repository, commit, registry).covered:
            return None
        [outcome] = run_local_ci(self.home, [repository], revisions={repository: commit},
                                 reporter=lambda line: None,
                                 source_environment=dict(self.tools.environment))
        return outcome

    def _wait_for_runs(self, definition: NpmPackage, commit: str, *, pull_request: int | None,
                       label: str) -> dict:
        """Answer for `commit` with local CI where it covers the repository, else wait for GitHub.

        Local CI runs the repository's own workflows here, in minutes, and posts its verdict as a
        commit status. Where it does not cover the repository, every workflow run on `commit` must
        finish green, and the pull request's checks too. A run that has not been indexed yet is
        indistinguishable from one that will never exist, so a push run is required to appear
        before anything counts as finished.
        """
        self.report(f"  running {label} CI locally on {commit[:8]}")
        outcome = self.local_ci(definition.repository, commit)
        if outcome is not None:
            if outcome.state != "passed":
                raise NpmReleaseError(
                    f"{definition.repository} {label} {commit[:8]} failed local CI: {outcome.detail}; "
                    f"the record is {outcome.record_path}")
            self.report(f"  {label} CI passed locally: {outcome.detail}")
            return {"commit": commit, "localCI": {"record": outcome.record_path,
                                                   "detail": outcome.detail,
                                                   "status": outcome.status}}
        self.report(f"  local CI does not cover {definition.repository}; waiting for GitHub")
        from org.metadatacenter.local_ci.gates import github_failed_to_start, rerun
        reruns: set = set()
        deadline = self.tools.clock() + self.ci_timeout_seconds
        last_report = 0.0
        while True:
            try:
                probe = self.ci_probe(definition.repository, commit, environment=self.tools.environment,
                                      sleeper=self.tools.sleep)
            except GithubCIProbeError as error:
                raise NpmReleaseError(str(error)) from error
            runs = [run for run in probe.runs if run.get("head_sha") == commit]
            push_runs = [run for run in runs if run.get("event") == "push"]
            failed = [run for run in runs if run.get("status") == "completed"
                      and run.get("conclusion") not in GREEN_CONCLUSIONS]
            unstarted = [run for run in failed if github_failed_to_start(
                definition.repository, run, runner=self._gh_command,
                environment=self.tools.environment)]
            for run in unstarted:
                if run.get("id") not in reruns:
                    reruns.add(run.get("id"))
                    refusal = rerun(definition.repository, run.get("id"),
                                    runner=self._gh_command, environment=self.tools.environment)
                    self.report(f"  GitHub failed to start {run.get('name')} on {commit[:8]}; "
                                + (f"its rerun was refused: {refusal}" if refusal else "rerunning it"))
            failed = [run for run in failed if run not in unstarted]
            if failed:
                detail = "; ".join(f"{run.get('name')} {run.get('conclusion')} {run_url(run)}" for run in failed)
                raise NpmReleaseError(f"{definition.repository} {label} {commit[:8]} failed CI: {detail}")
            pending = [run for run in runs if run.get("status") != "completed"] + unstarted
            rollup: list[dict] = []
            rollup_pending = False
            if pull_request is not None:
                view = self.tools.gh_json(
                    "pr", "view", str(pull_request), "--repo", definition.github,
                    "--json", "headRefOid,statusCheckRollup,state")
                if view.get("headRefOid") != commit:
                    raise NpmReleaseError(
                        f"pull request #{pull_request} now heads {str(view.get('headRefOid'))[:8]}, "
                        f"not the prepared commit {commit[:8]}")
                rollup = view.get("statusCheckRollup") or []
                for check in rollup:
                    if check.get("__typename") == "StatusContext":
                        state = check.get("state")
                        if state in ("FAILURE", "ERROR"):
                            raise NpmReleaseError(
                                f"pull request #{pull_request} check {check.get('context')} is {state}")
                        rollup_pending |= state != "SUCCESS"
                        continue
                    if check.get("status") != "COMPLETED":
                        rollup_pending = True
                    elif check.get("conclusion") not in PR_CHECK_GREEN:
                        raise NpmReleaseError(
                            f"pull request #{pull_request} check {check.get('name')} concluded "
                            f"{check.get('conclusion')}: {check.get('detailsUrl')}")
                rollup_pending |= not rollup
            if push_runs and not pending and not rollup_pending:
                return {
                    "commit": commit,
                    "runs": sorted(({"id": run.get("id"), "name": run.get("name"), "event": run.get("event"),
                                     "conclusion": run.get("conclusion"), "url": run_url(run)}
                                    for run in runs), key=lambda row: (row["event"], row["name"] or "")),
                    "pullRequestChecks": len(rollup),
                }
            remaining = deadline - self.tools.clock()
            if remaining <= 0:
                raise NpmReleaseWait(
                    f"{definition.repository} CI on {commit[:8]} is still running; "
                    "resume once it finishes")
            if self.tools.clock() - last_report >= 300:
                names = ", ".join(sorted({str(run.get("name")) for run in pending})) or "not yet indexed"
                self.report(f"  waiting for {label} CI on {commit[:8]}: {names}")
                last_report = self.tools.clock()
            self.tools.sleep(min(self.ci_poll_seconds, max(1.0, remaining)))

    # Package stages --------------------------------------------------------------------------

    def _prepare(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        repository = self.repository(definition)
        base = record["baseCommit"]
        branch = self.tools.git(repository, "rev-parse", "--abbrev-ref", "HEAD")
        head = self.tools.git(repository, "rev-parse", "HEAD")
        if branch != "develop" or head != base:
            raise NpmReleaseError(
                f"{definition.repository} is at {branch} {head[:8]}, not develop {base[:8]} as planned")
        allowed = self._allowed_paths(definition)
        dirty = self._dirty(repository)
        stray = sorted(set(dirty) - allowed)
        if stray:
            raise NpmReleaseError(
                f"{definition.repository} has changes the release does not make: {', '.join(stray)}")
        if dirty:
            # A previous attempt stopped partway. Its edits are the route's own and are redone.
            self.tools.git(repository, "checkout", "HEAD", "--", *dirty)

        evidence: dict = {}
        stamp_time = self.tools.local_now()
        log = self._logs(run, definition, "prepare")
        if definition.consumes_model:
            pins = [resolution["pin"] for resolution in self._model_resolutions(definition)]
            evidence["previousModelPins"] = pins
        self.tools.logged(
            ["npm", "version", record["version"], "--no-git-tag-version", "--allow-same-version"],
            repository, log)
        if definition.published_manifest:
            path = repository / definition.published_manifest
            path.write_text(published_manifest_name(path.read_text(), definition.public_name))
            published = json.loads(path.read_text())
            if published.get("version") != record["version"]:
                raise NpmReleaseError(
                    f"{definition.published_manifest} names {published.get('version')} after npm version, "
                    f"not {record['version']}; the repository's version hook did not run")
        if definition.consumes_model:
            self._install_model(definition, dependency_spec(record["modelVersion"], None), log)
            resolutions = self._model_resolutions(definition)
            for resolution in resolutions:
                if resolution["pin"] != record["modelVersion"] or resolution["version"] != record["modelVersion"]:
                    raise NpmReleaseError(
                        f"{definition.repository} {resolution['directory']} does not pin and resolve "
                        f"{MODEL_PUBLIC_NAME}@{record['modelVersion']}")
                if not str(resolution["resolved"]).startswith(PUBLIC_REGISTRY):
                    raise NpmReleaseError(
                        f"{definition.repository} {resolution['directory']} resolves the model from "
                        f"{resolution['resolved']}, not public npmjs")
            if len({(row["resolved"], row["integrity"]) for row in resolutions}) != 1:
                raise NpmReleaseError(f"{definition.repository}'s dependency graphs resolve different model tarballs")
            evidence["model"] = {"version": record["modelVersion"], "integrity": resolutions[0]["integrity"]}
        if definition.changelog:
            path = repository / definition.changelog
            entry = changelog_entry(record["version"], stamp_time.date().isoformat(),
                                    record.get("modelVersion"), record["notes"])
            path.write_text(insert_changelog_entry(path.read_text(), record["version"], entry))
        if definition.load_trace:
            path = repository / definition.load_trace
            path.write_text(stamp_load_trace(path.read_text(), stamp_time.strftime("%Y-%m-%d %H:%M")))
        changed = self._dirty(repository)
        stray = sorted(set(changed) - allowed)
        if stray:
            raise NpmReleaseError(
                f"preparing {definition.repository} changed paths the release does not own: {', '.join(stray)}")
        root = json.loads((repository / "package.json").read_text())
        if root.get("version") != record["version"]:
            raise NpmReleaseError(f"package.json names {root.get('version')}, not {record['version']}")
        evidence.update({"paths": changed, "stampedAt": stamp_time.isoformat(timespec="minutes")})
        return evidence

    def _gate(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        repository = self.repository(definition)
        paths = record["evidence"]["prepare"]["paths"]
        if self.tools.git(repository, "rev-parse", "HEAD") != record["baseCommit"]:
            raise NpmReleaseError(f"{definition.repository} HEAD moved after the release was prepared")
        if sorted(self._dirty(repository)) != sorted(paths):
            raise NpmReleaseError(f"{definition.repository}'s prepared changes are not the ones recorded")
        tree = self._tree_of(repository, paths)
        log = self._logs(run, definition, "gate")
        for directory in definition.installs:
            cwd = repository if directory == "." else repository / directory
            self.tools.logged(["npm", "ci"], cwd, log)
        for command in definition.gate:
            self.tools.logged(list(command), repository, log)
        if definition.framework_demos:
            self.tools.logged([*self.cli, "test", "cee-demos", "--cee-package",
                               str(repository / definition.stage_directory)], repository, log)
        if self._tree_of(repository, paths) != tree or sorted(self._dirty(repository)) != sorted(paths):
            raise NpmReleaseError(f"the {definition.id} gate changed tracked files; the gated tree is not the prepared one")
        return {"tree": tree, "log": str(log)}

    def _commit_message(self, definition: NpmPackage, record: dict) -> str:
        if definition.consumes_model:
            return (f"Prepare {definition.title} release {record['version']} with model library "
                    f"{record['modelVersion']}")
        return f"Prepare {definition.title} release {record['version']}"

    def _commit(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        repository = self.repository(definition)
        tree = record["evidence"]["gate"]["tree"]
        message = self._commit_message(definition, record)
        head = self.tools.git(repository, "rev-parse", "HEAD")
        if head != record["baseCommit"]:
            parent = self.tools.git(repository, "rev-parse", "HEAD^")
            if (parent == record["baseCommit"] and self.tools.git(repository, "rev-parse", "HEAD^{tree}") == tree
                    and self.tools.git(repository, "log", "-1", "--format=%s") == message):
                return {"commit": head, "reused": True}
            raise NpmReleaseError(f"{definition.repository} HEAD moved to {head[:8]} after the gate")
        paths = record["evidence"]["prepare"]["paths"]
        if self._tree_of(repository, paths) != tree:
            raise NpmReleaseError(f"{definition.repository}'s index no longer holds the gated tree")
        self.tools.git(repository, "commit", "-m", message)
        commit = self.tools.git(repository, "rev-parse", "HEAD")
        if self.tools.git(repository, "rev-parse", "HEAD^{tree}") != tree:
            raise NpmReleaseError("the preparation commit's tree is not the gated tree; a commit hook changed it")
        return {"commit": commit, "message": message}

    def _push(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        repository = self.repository(definition)
        commit = record["evidence"]["commit"]["commit"]
        remote = self._remote_ref(repository, "refs/heads/develop")
        if remote == commit:
            return {"commit": commit, "reused": True}
        if remote != record["baseCommit"]:
            raise NpmReleaseError(
                f"origin develop of {definition.repository} moved to {str(remote)[:8]} since the release was "
                f"planned from {record['baseCommit'][:8]}; abandon this run and plan again")
        self.tools.git(repository, "push", "origin", f"{commit}:refs/heads/develop")
        if self._remote_ref(repository, "refs/heads/develop") != commit:
            raise NpmReleaseError("origin develop does not hold the preparation commit after the push")
        return {"commit": commit}

    def _pull_request(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        commit = record["evidence"]["commit"]["commit"]
        rows = self.tools.gh_json(
            "pr", "list", "--repo", definition.github, "--base", "main", "--head", "develop",
            "--state", "open", "--json", "number,headRefOid,url")
        if not rows:
            title = f"Release {definition.title} {record['version']}"
            body = (f"Prepared by `cedarcli release npm`. After this merges, the route publishes "
                    f"`{definition.public_name}@{record['version']}` from `main` to npmjs.")
            self.tools.gh("pr", "create", "--repo", definition.github, "--base", "main",
                          "--head", "develop", "--title", title, "--body", body)
        for delay in (*self.registry_delays[:4], None):
            rows = self.tools.gh_json(
                "pr", "list", "--repo", definition.github, "--base", "main", "--head", "develop",
                "--state", "open", "--json", "number,headRefOid,url")
            if rows and rows[0].get("headRefOid") == commit:
                return {"number": rows[0]["number"], "url": rows[0]["url"]}
            if delay is None:
                break
            self.tools.sleep(delay)
        raise NpmReleaseError(
            f"no open pull request from develop to main heads {commit[:8]} in {definition.github}")

    def _checks(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        commit = record["evidence"]["commit"]["commit"]
        number = record["evidence"]["pull-request"]["number"]
        return self._wait_for_runs(definition, commit, pull_request=number, label="preparation")

    def _merge(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        repository = self.repository(definition)
        commit = record["evidence"]["commit"]["commit"]
        number = str(record["evidence"]["pull-request"]["number"])
        view = self.tools.gh_json("pr", "view", number, "--repo", definition.github,
                                  "--json", "state,mergeCommit,headRefOid")
        if view.get("state") == "OPEN":
            self.tools.gh("pr", "merge", number, "--repo", definition.github, "--merge",
                          "--match-head-commit", commit)
        merge = None
        for delay in (*self.registry_delays[:4], None):
            view = self.tools.gh_json("pr", "view", number, "--repo", definition.github,
                                      "--json", "state,mergeCommit,headRefOid")
            merge = (view.get("mergeCommit") or {}).get("oid")
            if view.get("state") == "MERGED" and merge:
                break
            if view.get("state") == "CLOSED":
                raise NpmReleaseError(f"pull request #{number} was closed without merging")
            if delay is None:
                raise NpmReleaseError(f"pull request #{number} does not report a merge commit")
            self.tools.sleep(delay)
        if view.get("headRefOid") != commit:
            raise NpmReleaseError(f"pull request #{number} merged {str(view.get('headRefOid'))[:8]}, not {commit[:8]}")
        self.tools.git(repository, "fetch", "origin", "main")
        if self.tools.git(repository, "rev-parse", "origin/main") != merge:
            raise NpmReleaseError(f"origin main of {definition.repository} is not the merge commit {merge[:8]}")
        if self.tools.git(repository, "rev-parse", f"{merge}^{{tree}}") != record["evidence"]["gate"]["tree"]:
            raise NpmReleaseError(
                f"main's merge commit {merge[:8]} does not hold the gated tree; main had diverged from develop")
        parents = self.tools.git(repository, "rev-list", "--parents", "-n", "1", merge).split()[1:]
        if commit not in parents:
            raise NpmReleaseError(f"the merge commit {merge[:8]} is not a merge of the preparation commit")
        return {"commit": merge}

    def _check_staged(self, definition: NpmPackage, record: dict) -> Path:
        repository = self.repository(definition)
        stage = repository / definition.stage_directory
        manifest = json.loads((stage / "package.json").read_text())
        problems = []
        if manifest.get("name") != definition.public_name:
            problems.append(f"name {manifest.get('name')!r}")
        if manifest.get("version") != record["version"]:
            problems.append(f"version {manifest.get('version')!r}")
        if manifest.get("private"):
            problems.append("private")
        if manifest.get("publishConfig"):
            problems.append("a publishConfig")
        scoped = [name for section in ("dependencies", "peerDependencies", "optionalDependencies")
                  for name in (manifest.get(section) or {}) if name.startswith("@org.metadatacenter/")]
        if scoped:
            problems.append(f"Nexus-only dependencies {', '.join(sorted(scoped))}")
        if (stage / "README.md").read_bytes() != (repository / "README.md").read_bytes():
            problems.append("a README other than the repository's")
        if problems:
            raise NpmReleaseError(f"the staged {definition.id} package has {'; '.join(problems)}")
        return stage

    def _build_main(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        repository = self.repository(definition)
        merge = record["evidence"]["merge"]["commit"]
        if self._dirty(repository):
            raise NpmReleaseError(f"{definition.repository} has uncommitted changes before the main build")
        self.tools.git(repository, "checkout", "--detach", merge)
        log = self._logs(run, definition, "build-main")
        for command in definition.main_build:
            self.tools.logged(list(command), repository, log)
        stage = self._check_staged(definition, record)
        destination = self._attempt(run, definition) / "tarball"
        shutil.rmtree(destination, ignore_errors=True)
        try:
            tarball, inspection = pack_and_inspect(stage, destination, environment=self.tools.environment)
        except NpmPackageError as error:
            raise NpmReleaseError(str(error)) from error
        if definition.framework_demos:
            self.tools.logged([*self.cli, "test", "cee-demos", "--cee-package", str(tarball)], repository, log)
        dry = self.tools.npm(
            None, "publish", str(tarball), "--dry-run", f"--registry={PUBLIC_REGISTRY}",
            "--tag", "latest", "--access", "public")
        text = f"{dry.stdout}\n{dry.stderr}"
        identity = f"{definition.public_name}@{record['version']}"
        if identity not in text or PUBLIC_REGISTRY not in text or "tag latest" not in text:
            raise NpmReleaseError(f"the dry run does not publish {identity} to {PUBLIC_REGISTRY} under latest")
        if self.tools.git(repository, "status", "--porcelain=v1", "--untracked-files=no"):
            raise NpmReleaseError(f"the {definition.id} main build changed tracked files")
        return {"tarball": str(tarball), "sha256": inspection.sha256, "integrity": inspection.integrity,
                "bytes": tarball.stat().st_size, "log": str(log)}

    def _publish(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        built = record["evidence"]["build-main"]
        identity = f"{definition.public_name}@{record['version']}"
        existing = self.tools.npm_view(identity, PUBLIC_REGISTRY)
        if existing:
            if (existing.get("dist") or {}).get("integrity") != built["integrity"]:
                raise NpmReleaseError(
                    f"{identity} is already on npmjs with other bytes; npm versions are immutable, "
                    "so this release needs a new version")
            return {"published": "already", "identity": identity}
        tarball = Path(built["tarball"])
        content = tarball.read_bytes()
        if sha256(content) != built["sha256"]:
            raise NpmReleaseError(f"{tarball} changed since the main build")
        whoami = self.tools.npm(None, "whoami", f"--registry={PUBLIC_REGISTRY}", check=False)
        if whoami.returncode or whoami.stdout.strip() != NPM_ACCOUNT:
            raise NpmReleaseError(f"npm is not signed in to npmjs as {NPM_ACCOUNT}: {whoami.detail}")
        result = self.tools.npm(None, "publish", str(tarball), f"--registry={PUBLIC_REGISTRY}",
                                "--tag", "latest", "--access", "public", check=False)
        if result.returncode:
            if PUBLISH_CONFLICT.search(f"{result.stdout}\n{result.stderr}"):
                return {"published": "conflict", "identity": identity}
            raise NpmReleaseError(f"npm publish {identity} failed: {result.detail}")
        return {"published": "now", "identity": identity}

    def _verify(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        """Prove npmjs serves the gated bytes as the latest version, retrying while it catches up."""
        built = record["evidence"]["build-main"]
        identity = f"{definition.public_name}@{record['version']}"
        readme_title = (self.repository(definition) / "README.md").read_text().splitlines()[0]
        last = "npmjs has not served the version yet"
        for delay in (*self.registry_delays, None):
            document = self.tools.npm_view(identity, PUBLIC_REGISTRY)
            if document:
                dist = document.get("dist") or {}
                if dist.get("integrity") != built["integrity"]:
                    raise NpmReleaseError(f"npmjs serves {identity} with integrity {dist.get('integrity')}, "
                                          f"not the gated {built['integrity']}")
                content = self.tools.download(dist.get("tarball") or "")
                inspection = inspect_tarball(content, identity)
                if inspection.integrity != built["integrity"] or inspection.sha256 != built["sha256"]:
                    raise NpmReleaseError(f"the tarball npmjs serves for {identity} is not the gated tarball")
                tags = self.tools.npm_view(f"{definition.public_name}", PUBLIC_REGISTRY) or {}
                latest = (tags.get("dist-tags") or {}).get("latest")
                readme = self.tools.npm(None, "view", identity, "readme", f"--registry={PUBLIC_REGISTRY}",
                                        check=False).stdout.strip()
                if latest == record["version"] and readme.splitlines()[:1] == [readme_title]:
                    return {"integrity": inspection.integrity, "sha256": inspection.sha256,
                            "tarball": dist.get("tarball"), "latest": latest}
                last = f"latest is {latest}, the readme opens {readme.splitlines()[:1]}"
            if delay is None:
                break
            self.report(f"  npmjs: {last}; asking again in {delay:g}s")
            self.tools.sleep(delay)
        raise NpmReleaseWait(f"{identity} is published but not yet verified ({last}); resume to ask again")

    def _tag(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        repository = self.repository(definition)
        merge = record["evidence"]["merge"]["commit"]
        tag = definition.tag(record["version"])
        remote = self._remote_ref(repository, f"refs/tags/{tag}")
        if remote:
            if remote != merge:
                raise NpmReleaseError(f"{tag} on origin names {remote[:8]}, not the released {merge[:8]}")
            return {"tag": tag, "commit": merge, "reused": True}
        local = self.tools.git_result(repository, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}")
        if local.returncode == 0 and local.stdout.strip() != merge:
            raise NpmReleaseError(f"the local {tag} names {local.stdout.strip()[:8]}, not {merge[:8]}")
        if local.returncode:
            self.tools.git(repository, "tag", tag, merge)
        self.tools.git(repository, "push", "origin", f"refs/tags/{tag}")
        if self._remote_ref(repository, f"refs/tags/{tag}") != merge:
            raise NpmReleaseError(f"origin does not hold {tag} at {merge[:8]} after the push")
        return {"tag": tag, "commit": merge}

    def _notes(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        """Draft the GitHub release on the tag, for the operator to review and publish."""
        tag = definition.tag(record["version"])
        if not record.get("notes"):
            return {"draft": None}
        existing = self.tools.gh("release", "view", tag, "--repo", definition.github,
                                 "--json", "url,isDraft", check=False)
        if existing.returncode == 0:
            return {"draft": json.loads(existing.stdout).get("url"), "reused": True}
        identity = f"{definition.public_name}@{record['version']}"
        lines = [f"[`{identity}`](https://www.npmjs.com/package/{definition.public_name}/v/{record['version']})", ""]
        if record.get("modelVersion"):
            lines += [f"Aligns with `{MODEL_PUBLIC_NAME}@{record['modelVersion']}`.", ""]
        body = self._attempt(run, definition) / "release-notes.md"
        body.write_text("\n".join(lines) + record["notes"])
        self.tools.gh("release", "create", tag, "--repo", definition.github, "--draft", "--verify-tag",
                      "--title", f"{definition.title} {record['version']}", "--notes-file", str(body))
        view = self.tools.gh_json("release", "view", tag, "--repo", definition.github, "--json", "url,isDraft")
        if not view.get("isDraft"):
            raise NpmReleaseError(f"the GitHub release {tag} is not a draft")
        return {"draft": view.get("url")}

    def _development_model_pin(self, definition: NpmPackage, record: dict) -> str:
        """The scoped model the next development version builds against.

        The operator may name one. Otherwise develop returns to the pin it held before the release,
        provided Nexus still serves it: its development packages are cleaned after a few days, and a
        pin to a deleted one fails every install.
        """
        chosen = record.get("developmentModelVersion")
        if not chosen:
            previous = {pin for pin in record["evidence"]["prepare"].get("previousModelPins", []) if pin}
            prefix = f"npm:{MODEL_DEV_NAME}@"
            scoped = {pin[len(prefix):] for pin in previous if pin.startswith(prefix)}
            if len(scoped) != 1:
                raise NpmReleaseError(
                    f"{definition.repository} did not pin one development model before the release "
                    f"({', '.join(sorted(previous)) or 'none'}); resume with --dev-model-version")
            chosen = scoped.pop()
        if not self.tools.reachable(nexus_tarball_url(MODEL_DEV_NAME, chosen)):
            raise NpmReleaseError(
                f"Nexus no longer serves {MODEL_DEV_NAME}@{chosen}; resume with --dev-model-version "
                "naming a published development model")
        return chosen

    def _develop(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        repository = self.repository(definition)
        merge = record["evidence"]["merge"]["commit"]
        prepared = record["evidence"]["commit"]["commit"]
        message = f"Advance {definition.title} to the next development version"
        self.tools.git(repository, "fetch", "origin", "develop", "main")
        remote = self.tools.git(repository, "rev-parse", "origin/develop")
        self.tools.git(repository, "checkout", "develop")
        head = self.tools.git(repository, "rev-parse", "HEAD")
        # Resume: the advance commit exists, locally or already on origin.
        for candidate in (remote, head):
            if (self.tools.git(repository, "log", "-1", "--format=%s", candidate) == message
                    and self.tools.git_result(repository, "merge-base", "--is-ancestor", merge, candidate).returncode == 0):
                return self._push_advance(definition, record, candidate)
        dirty = self._dirty(repository)
        stray = sorted(set(dirty) - self._allowed_paths(definition))
        if stray:
            raise NpmReleaseError(f"{definition.repository} develop has changes the release does not make: {', '.join(stray)}")
        if dirty:
            self.tools.git(repository, "checkout", "HEAD", "--", *dirty)
        if self.tools.git_result(repository, "merge-base", "--is-ancestor", remote, head).returncode:
            self.tools.git(repository, "merge", "--ff-only", "origin/develop")
        head = self.tools.git(repository, "rev-parse", "HEAD")
        if self.tools.git_result(repository, "merge-base", "--is-ancestor", merge, head).returncode:
            if head == prepared:
                self.tools.git(repository, "merge", "--ff-only", merge)
            else:
                # Others pushed to develop while the release ran. The merge commit adds nothing to
                # the tree, so taking it with a merge of develop's own changes nothing either.
                before = self.tools.git(repository, "rev-parse", "HEAD^{tree}")
                self.tools.git(repository, "merge", "--no-edit", "--no-ff", merge)
                if self.tools.git(repository, "rev-parse", "HEAD^{tree}") != before:
                    raise NpmReleaseError("taking the release merge into develop changed develop's tree")
        head = self.tools.git(repository, "rev-parse", "HEAD")
        short = self.tools.git(repository, "rev-parse", "--short", "HEAD")
        # The workflow that publishes a development package derives its version from the commit it
        # builds, in UTC. That commit does not exist yet, so this string names the one below it.
        seconds = int(self.tools.git(repository, "show", "-s", "--format=%ct", "HEAD"))
        date = dt.datetime.fromtimestamp(seconds, dt.timezone.utc).strftime("%Y%m%d")
        development_version = f"{record['nextBase']}-dev.{date}.{short}"
        log = self._logs(run, definition, "develop")
        self.tools.logged(["npm", "version", development_version, "--no-git-tag-version"], repository, log)
        evidence: dict = {"version": development_version, "base": head}
        if definition.published_manifest:
            path = repository / definition.published_manifest
            path.write_text(published_manifest_name(path.read_text(), definition.dev_name))
        if definition.consumes_model:
            pin = self._development_model_pin(definition, record)
            self._install_model(definition, dependency_spec(None, pin), log)
            for resolution in self._model_resolutions(definition):
                if resolution["version"] != pin:
                    raise NpmReleaseError(f"{resolution['directory']} resolves {resolution['version']}, not {pin}")
            evidence["modelPin"] = pin
        if definition.load_trace:
            path = repository / definition.load_trace
            stamp = f"{self.tools.local_now().strftime('%Y-%m-%d %H:%M')} {short}"
            path.write_text(stamp_load_trace(path.read_text(), stamp))
        for command in definition.development_check:
            self.tools.logged(list(command), repository, log)
        changed = self._dirty(repository)
        stray = sorted(set(changed) - self._allowed_paths(definition))
        if stray:
            raise NpmReleaseError(f"advancing {definition.repository} changed paths it does not own: {', '.join(stray)}")
        self.tools.git(repository, "add", "--", *changed)
        self.tools.git(repository, "commit", "-m", message)
        advance = self.tools.git(repository, "rev-parse", "HEAD")
        return {**evidence, **self._push_advance(definition, record, advance), "paths": changed}

    def _push_advance(self, definition: NpmPackage, record: dict, advance: str) -> dict:
        repository = self.repository(definition)
        remote = self._remote_ref(repository, "refs/heads/develop")
        if remote != advance:
            if self.tools.git_result(repository, "merge-base", "--is-ancestor", str(remote), advance).returncode:
                raise NpmReleaseError(
                    f"origin develop moved to {str(remote)[:8]} while the release advanced it; "
                    f"merge it into develop and push {advance[:8]}'s change by hand")
            self.tools.git(repository, "push", "origin", f"{advance}:refs/heads/develop")
            if self._remote_ref(repository, "refs/heads/develop") != advance:
                raise NpmReleaseError("origin develop does not hold the advance commit after the push")
        manifest = json.loads(self.tools.git(repository, "show", f"{advance}:package.json"))
        return {"commit": advance, "version": manifest.get("version")}

    def _development_checks(self, definition: NpmPackage, record: dict, run: dict) -> dict:
        commit = record["evidence"]["develop"]["commit"]
        return self._wait_for_runs(definition, commit, pull_request=None, label="development")

    # Run stage -------------------------------------------------------------------------------

    def _baselines(self, run: dict) -> dict:
        """Refresh the reviewed lock baselines the release moved, and commit only that file."""
        repositories = sorted({PACKAGES[record["package"]].repository for record in run["packages"]})
        if run.get("deferBaselines"):
            return {"deferred": True, "repositories": repositories}
        if self.platform_release_active and self.platform_release_active():
            raise NpmReleaseWait(
                "a platform release is active, and committing to cedar-development would move a "
                "repository its train captured; resume once that release is accepted")
        development = self.home / "cedar-development"
        path = "ops/frontend-train.json"
        if self.tools.git(development, "rev-parse", "--abbrev-ref", "HEAD") != "develop":
            raise NpmReleaseError("cedar-development is not on develop")
        if self.tools.git(development, "status", "--porcelain=v1", "--", path):
            raise NpmReleaseError(f"cedar-development has uncommitted changes to {path}")
        self.tools.git(development, "fetch", "origin", "develop")
        if self.tools.git_result(development, "merge-base", "--is-ancestor", "origin/develop", "HEAD").returncode:
            self.tools.git(development, "merge", "--ff-only", "origin/develop")
        if self.baseline_refresher is None:
            raise NpmReleaseError("no lock baseline refresher is configured")
        code = self.baseline_refresher(repositories)
        if code:
            raise NpmReleaseError(f"cedarcli publish baselines --refresh failed with exit {code}")
        if not self.tools.git(development, "status", "--porcelain=v1", "--", path):
            return {"changed": False, "repositories": repositories}
        titles = " and ".join(PACKAGES[record["package"]].title for record in run["packages"])
        self.tools.git(development, "commit", "-m", f"Refresh the npm audit baselines the {titles} release moved",
                       "--", path)
        commit = self.tools.git(development, "rev-parse", "HEAD")
        self.tools.git(development, "push", "origin", "HEAD:refs/heads/develop")
        if self._remote_ref(development, "refs/heads/develop") != commit:
            raise NpmReleaseError("origin develop of cedar-development does not hold the baseline commit")
        return {"changed": True, "commit": commit, "repositories": repositories}

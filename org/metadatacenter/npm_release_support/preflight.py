"""Every precondition a run depends on, settled before the first stage changes anything."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from org.metadatacenter.npm_policy import npm_user_config_findings
from org.metadatacenter.npm_release_support.definitions import (
    MODEL_PUBLIC_NAME,
    NPM_ACCOUNT,
    PACKAGES,
    PUBLIC_REGISTRY,
)
from org.metadatacenter.npm_release_support.edits import development_model_pin
from org.metadatacenter.npm_release_support.errors import NpmReleaseError
from org.metadatacenter.npm_release_support.requests import (
    PackageRequest, RunRequest, read_notes, version_key)
from org.metadatacenter.npm_release_support.route import nexus_tarball_url
from org.metadatacenter.npm_release_support.tools import Tools
from org.metadatacenter.release_support.policy import REQUIRED_NODE_VERSION


class NpmReleasePreflight:
    """Answers whether a run can start, as a list of reasons it cannot.

    Every check reads; none writes. Fetching updates remote-tracking refs and nothing else.
    """

    def __init__(self, cedar_home: Path, tools: Tools, *, home: Path | None = None):
        self.cedar_home = Path(cedar_home)
        self.tools = tools
        self.user_home = home or Path.home()

    def rebase(self, run: dict, notes: dict[str, str] | None = None) -> list[str]:
        """Plan again every package the run has not started, from develop as it now stands.

        A run carries its packages one after another, and an earlier package can spend hours
        waiting for CI. A later package's develop may move meanwhile, and its prepare stage then
        refuses a head the plan never checked. Re-planning such a package runs its preflight again
        against the new head and starts it from there. A package that has begun preparing is left
        alone: its preparation commit already names its base.
        """
        notes = dict(notes or {})
        unstarted = [record for record in run["packages"] if not record.get("completed")]
        for name in notes:
            if not any(record["package"] == name for record in unstarted):
                raise NpmReleaseError(f"{name} is not a package this run has yet to start")
        model_in_run = any(record["package"] == "model" for record in run["packages"])
        requests = {record["package"]: PackageRequest(
            record["package"], record["version"], record["nextBase"], record.get("modelVersion"),
            record.get("developmentModelVersion"),
            notes.get(record["package"], record.get("notes"))) for record in unstarted}
        request = RunRequest(tuple(requests.values()), bool(run.get("deferBaselines")), model_in_run)
        lines = []
        findings: list[str] = []
        planned = {}
        for record in unstarted:
            replanned, package_findings = self._package(requests[record["package"]], request)
            findings += package_findings
            planned[record["package"]] = replanned
        if findings:
            raise NpmReleaseError("cannot plan the unstarted packages again:\n  " + "\n  ".join(findings))
        for record in unstarted:
            base = planned[record["package"]]["baseCommit"]
            if base != record.get("baseCommit"):
                lines.append(f"{record['repository']}: develop {str(record.get('baseCommit'))[:8]} -> {base[:8]}")
            record["baseCommit"] = base
            record["notes"] = requests[record["package"]].notes
            record["rebasedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
            record["failure"] = None
        return lines

    def run(self, request: RunRequest) -> tuple[list[str], list[dict]]:
        findings: list[str] = []
        findings += self._toolchain()
        records = []
        for package in request.packages:
            record, package_findings = self._package(package, request)
            findings += package_findings
            records.append(record)
        return findings, records

    def _toolchain(self) -> list[str]:
        findings = []
        node = self.tools.run(["node", "--version"], check=False)
        if node.returncode or node.stdout.strip() != REQUIRED_NODE_VERSION:
            findings.append(f"node is {node.stdout.strip() or 'missing'}, not {REQUIRED_NODE_VERSION}")
        whoami = self.tools.npm(None, "whoami", f"--registry={PUBLIC_REGISTRY}", check=False)
        if whoami.returncode or whoami.stdout.strip() != NPM_ACCOUNT:
            findings.append(
                f"npm is not signed in to npmjs as {NPM_ACCOUNT} ({whoami.detail}); place a granular "
                "access token in ~/.npmrc")
        try:
            for finding in npm_user_config_findings(self.user_home / ".npmrc"):
                if finding.severity == "fail":
                    findings.append(f"~/.npmrc: {finding.message}; {finding.remedy}")
        except ValueError as error:
            findings.append(str(error))
        auth = self.tools.gh("auth", "status", check=False)
        if auth.returncode:
            findings.append(f"gh is not authenticated: {auth.detail}")
        return findings

    def _package(self, package, request: RunRequest) -> tuple[dict, list[str]]:
        definition = PACKAGES[package.package]
        repository = self.cedar_home / definition.repository
        findings: list[str] = []
        record = {**package.as_record(), "repository": definition.repository, "completed": [],
                  "evidence": {}, "stage": None}
        label = definition.repository
        if not (repository / ".git").exists():
            return record, [f"{label} is not checked out at {repository}"]
        tools = self.tools
        branch = tools.git(repository, "rev-parse", "--abbrev-ref", "HEAD", check=False)
        if branch != "develop":
            findings.append(f"{label} is on {branch or 'no branch'}, not develop")
        if tools.git(repository, "status", "--porcelain=v1", "--untracked-files=all", check=False):
            findings.append(f"{label} has uncommitted or untracked files; npm does not record what it packed")
        fetched = tools.git_result(repository, "fetch", "origin", "develop", "main", "--tags")
        if fetched.returncode:
            findings.append(f"{label} cannot fetch origin: {fetched.detail}")
            return record, findings
        head = tools.git(repository, "rev-parse", "HEAD")
        remote = tools.git(repository, "rev-parse", "origin/develop")
        record["baseCommit"] = head
        if head != remote:
            findings.append(f"{label} develop {head[:8]} is not origin/develop {remote[:8]}")
        if tools.git_result(repository, "merge-base", "--is-ancestor", "origin/main", "origin/develop").returncode:
            findings.append(f"{label} main has commits develop lacks; incorporate main on develop first")
        tag = definition.tag(package.version)
        if tools.git_result(repository, "rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0:
            findings.append(f"{label} already has the tag {tag}")
        open_rows = tools.gh_json("pr", "list", "--repo", definition.github, "--base", "main",
                                  "--head", "develop", "--state", "open", "--json", "number")
        if open_rows:
            findings.append(f"{label} already has an open pull request from develop to main "
                            f"(#{open_rows[0]['number']}); merge or close it first")

        if definition.changelog:
            findings += self._unreleased_notes(repository / definition.changelog, label, package.package)

        identity = f"{definition.public_name}@{package.version}"
        if tools.npm_view(identity, PUBLIC_REGISTRY):
            findings.append(f"{identity} is already on npmjs, and npm versions are immutable")
        latest_document = tools.npm_view(definition.public_name, PUBLIC_REGISTRY)
        latest = ((latest_document or {}).get("dist-tags") or {}).get("latest")
        if latest and version_key(package.version) <= version_key(latest):
            findings.append(f"{identity} would not be newer than the public latest {latest}")
        record["previousPublicVersion"] = latest

        if definition.consumes_model and not request.model_in_run:
            model = tools.npm_view(f"{MODEL_PUBLIC_NAME}@{package.model_version}", PUBLIC_REGISTRY)
            if not model:
                findings.append(
                    f"{MODEL_PUBLIC_NAME}@{package.model_version} is not on npmjs; release the model "
                    "first, or in the same run")
        if definition.consumes_model and not package.development_model_version:
            pins = set()
            for directory in definition.model_manifests:
                prefix = repository if directory == "." else repository / directory
                pins.add(development_model_pin(json.loads((prefix / "package.json").read_text())))
            if len(pins) != 1 or None in pins:
                findings.append(
                    f"{label} does not pin one development model in every graph; give --dev-model-version")
            elif not tools.reachable(nexus_tarball_url("@org.metadatacenter/" + MODEL_PUBLIC_NAME, next(iter(pins)))):
                findings.append(
                    f"Nexus no longer serves the development model {label} pins ({next(iter(pins))}); "
                    "give --dev-model-version naming a published one")
        findings += self._locked_nexus_packages(repository, definition, label)
        return record, findings

    @staticmethod
    def _unreleased_notes(changelog: Path, label: str, package: str) -> list[str]:
        """Refuse a changelog whose Unreleased section still holds notes.

        The route writes the release entry below `## [Unreleased]` and leaves that section as it
        is, because the platform release proves the public package against its train's by cutting
        the new entry out again. Notes left there would then read as unreleased above the release
        that shipped them, so they belong in the run's notes, and the section goes first.

        Only the section that opens the changelog counts. The route writes below that one alone,
        and a section under an earlier release, as CEE's holds under 2.0.17, stays below the
        entry.
        """
        if not changelog.is_file():
            return []
        text = changelog.read_text()
        first = re.search(r"^## \[", text, re.MULTILINE)
        match = first and re.match(r"## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)", text[first.start():],
                                   re.MULTILINE | re.DOTALL)
        if not match or not match.group(1).strip():
            return []
        return [f"{label} {changelog.name} holds notes under ## [Unreleased], which would stay above "
                f"the release entry; move them into --notes {package}=<file> and remove the "
                "section on develop first"]

    def _locked_nexus_packages(self, repository: Path, definition, label: str) -> list[str]:
        """Refuse a lock naming a Nexus package Nexus no longer serves.

        Nexus deletes development packages after a few days, and the gate begins with `npm ci`, so
        a lock naming a deleted design-token build fails the release an hour in rather than now.
        The model library's own Nexus pin is replaced by the release, so it is not asked about.
        """
        findings = []
        for directory in sorted(set(definition.installs) | set(definition.model_manifests)):
            prefix = repository if directory == "." else repository / directory
            lock_path = prefix / "package-lock.json"
            if not lock_path.exists():
                continue
            lock = json.loads(lock_path.read_text())
            for name, entry in sorted((lock.get("packages") or {}).items()):
                resolved = entry.get("resolved") or ""
                if "nexus.bmir.stanford.edu" not in resolved:
                    continue
                if definition.consumes_model and name == f"node_modules/{MODEL_PUBLIC_NAME}":
                    continue
                if not self.tools.reachable(resolved):
                    findings.append(
                        f"{label} {lock_path.relative_to(repository)} locks {name.removeprefix('node_modules/')} "
                        f"{entry.get('version')}, which Nexus no longer serves; move the pin with "
                        "cedarcli publish components --apply first")
        return findings


def run_identifier(request: RunRequest, clock=None) -> str:
    moment = (clock or dt.datetime.now)(dt.timezone.utc)
    packages = "-".join(f"{row.package}-{row.version}" for row in request.packages)
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{packages}"

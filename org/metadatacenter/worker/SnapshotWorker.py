"""Reads what Nexus serves and what develop holds, and reports where the two disagree."""

import base64
import shutil
import subprocess
from datetime import timedelta

import requests
from rich.console import Console
from rich.table import Column, Table

from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.SnapshotFreshness import (
    DEFAULT_GRACE,
    SnapshotFinding,
    SnapshotState,
    evaluate,
    parse_commit_time,
    parse_last_updated,
)

console = Console()

DEFAULT_NEXUS = "https://nexus.bmir.stanford.edu/repository/snapshots"
GROUP_PATH = "org/metadatacenter"
ORGANIZATION = "metadatacenter"
HTTP_TIMEOUT = 20

STATE_ICON = {
    SnapshotState.CURRENT: "[green]current[/green]",
    SnapshotState.BEHIND: "[red]behind[/red]",
    SnapshotState.ABSENT: "[red]absent[/red]",
    SnapshotState.UNREADABLE: "[yellow]unreadable[/yellow]",
}

# Only these publish a Maven snapshot. The frontends publish to npm and the rest publish nothing, so
# asking Nexus about them would report an absence that is correct rather than a fault.
PUBLISHING_TYPES = (RepoType.JAVA, RepoType.JAVA_WRAPPER)


class SnapshotWorker:

    @staticmethod
    def check_snapshots(version=None, grace_hours=None, nexus=DEFAULT_NEXUS):
        """Compare every publishing repository's Nexus snapshot with the head commit on develop.

        Exits non-zero when any snapshot is behind its source or missing, which is the condition that
        leaves consumers building against an artifact nobody shipped.
        """
        if shutil.which("gh") is None:
            console.print("[red]gh is not on PATH, so the head commit on develop cannot be read.[/red]")
            console.print("Install the GitHub CLI and authenticate it with gh auth login.")
            return 1

        repositories = sorted(
            {repo.name for repo in GlobalContext.repos.get_list_all()
             if repo.repo_type in PUBLISHING_TYPES})

        version = version or SnapshotWorker._estate_version()
        if not version:
            console.print("[red]The estate version could not be read from cedar-parent on develop.[/red]")
            return 1

        grace = timedelta(hours=grace_hours) if grace_hours is not None else DEFAULT_GRACE

        table = Table(
            "Repository",
            Column(header="State", justify="center"),
            Column(header="Detail"),
        )
        findings = []
        for repository in repositories:
            finding = SnapshotWorker._evaluate_repository(repository, version, grace, nexus)
            findings.append(finding)
            table.add_row(repository, STATE_ICON[finding.state], finding.detail)

        failures = [finding for finding in findings if finding.is_failure]
        unreadable = [finding for finding in findings if finding.state == SnapshotState.UNREADABLE]

        caption = f"{len(findings) - len(failures) - len(unreadable)}/{len(findings)} snapshots current at {version}"
        if failures:
            caption += ("\n[red]" + str(len(failures)) + " not published from the current source: "
                        + ", ".join(finding.repository for finding in failures) + "[/red]")
        if unreadable:
            caption += ("\n[yellow]" + str(len(unreadable)) + " unreadable: "
                        + ", ".join(finding.repository for finding in unreadable) + "[/yellow]")
        table.caption = caption
        console.print(table)

        if failures:
            console.print(
                "\nA snapshot behind its source means every consumer resolves the artifact it "
                "replaced, while the repository's own branch looks green. Re-run that repository's "
                "failed CI run; if its deploy step failed against Nexus, the publication is what "
                "needs repeating, not the source.")
        return 1 if failures else 0

    @staticmethod
    def _evaluate_repository(repository, version, grace, nexus) -> SnapshotFinding:
        published_at, unreadable = SnapshotWorker._published_at(repository, version, nexus)
        if unreadable:
            return SnapshotFinding(repository, SnapshotState.UNREADABLE, unreadable)
        return evaluate(repository, version, published_at,
                        SnapshotWorker._committed_at(repository), grace)

    @staticmethod
    def _published_at(repository, version, nexus):
        """The instant Nexus records for this snapshot, and any reason it could not be read.

        A 404 is an answer rather than a failure: the coordinate exists in the estate's expectations
        and not in Nexus, which is the absence worth reporting.
        """
        url = f"{nexus}/{GROUP_PATH}/{repository}/{version}/maven-metadata.xml"
        try:
            response = requests.get(url, timeout=HTTP_TIMEOUT)
        except requests.RequestException as error:
            return None, f"Nexus could not be reached: {type(error).__name__}"
        if response.status_code == 404:
            return None, None
        if response.status_code != 200:
            return None, f"Nexus answered {response.status_code} for {version}"
        return parse_last_updated(response.text), None

    @staticmethod
    def _committed_at(repository):
        """The time of the head commit on develop, as GitHub reports it."""
        completed = subprocess.run(
            ["gh", "api", f"repos/{ORGANIZATION}/{repository}/commits/develop",
             "--jq", ".commit.committer.date"],
            capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            return None
        return parse_commit_time(completed.stdout)

    @staticmethod
    def _estate_version():
        """The version every repository is expected to publish.

        Read once from cedar-parent on develop rather than from each repository, because the estate
        holds one version and `cedarcli check versions` is what enforces that. A repository that has
        drifted shows here as an absent snapshot naming the version asked for, which says the same
        thing from the other side.
        """
        completed = subprocess.run(
            ["gh", "api", f"repos/{ORGANIZATION}/cedar-parent/contents/pom.xml?ref=develop",
             "--jq", ".content"],
            capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            return None
        try:
            pom = base64.b64decode(completed.stdout).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        # The first <version> under the project element is cedar-parent's own; it declares no parent.
        for line in pom.splitlines():
            stripped = line.strip()
            if stripped.startswith("<version>") and stripped.endswith("</version>"):
                return stripped[len("<version>"):-len("</version>")]
        return None

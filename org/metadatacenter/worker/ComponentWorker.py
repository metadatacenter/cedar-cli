"""Reads what each browser application pins, stages and creates, and reports where the three disagree."""
import hashlib
import json
import subprocess
from pathlib import Path

from rich.console import Console
from rich.table import Column, Table

from org.metadatacenter.util.ComponentFreshness import (
    ComponentState,
    Surface,
    dependency_pins,
    evaluate_bundle,
    evaluate_element,
    evaluate_pin,
    pinned_commit,
    referenced_elements,
    release_tag,
    unscoped,
)
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util

console = Console()

STATE_ICON = {
    ComponentState.CURRENT: "[green]current[/green]",
    ComponentState.BEHIND: "[yellow]behind[/yellow]",
    ComponentState.DIVERGED: "[red]diverged[/red]",
    ComponentState.UNRESOLVED: "[yellow]unresolved[/yellow]",
    ComponentState.OVERRIDDEN: "[yellow]overridden[/yellow]",
    ComponentState.MISMATCHED: "[red]mismatched[/red]",
    ComponentState.UNDEFINED: "[red]undefined[/red]",
}

# Where a host keeps the bundles it serves and the record of what it staged.
STAGED_MANIFEST = Path("app/components/manifest.json")

# Where a host's own code lives. The staged bundles sit under app/components and are the packages
# themselves, so reading them here would report every component's elements as referenced by
# every host that serves it.
SOURCE_GLOBS = ("app/**/*.mjs", "app/**/*.js", "app/**/*.html", "src/**/*.ts", "src/**/*.html")
EXCLUDED_PARTS = ("node_modules", "dist", "components")

# What clears a failure, for a gate that has room for one line rather than a table.
COMPONENT_REMEDY = ("publish the component's current source, advance the host's pin onto that snapshot, "
                    "and stage the locked package")


class ComponentGateError(Exception):
    """The comparison could not be made at all, rather than made and failed."""


class ComponentWorker:

    @staticmethod
    def findings(root=None):
        """Every comparison for every browser application, reported to nobody.

        The release and train gates call this rather than the command: a preflight needs the
        verdicts and prints its own report, and a table written to a console it does not own
        would arrive in the middle of somebody else's.
        """
        workspace = root or Util.cedar_home
        if not isinstance(workspace, (str, Path)) or not str(workspace).strip():
            raise ComponentGateError("CEDAR_HOME does not name a workspace to compare")
        components = ComponentWorker._component_index(workspace)
        if not components:
            raise ComponentGateError(
                "no component packages were found in the workspace; check CEDAR_HOME")
        findings = []
        for name, directory in sorted(components.items()):
            findings.extend(ComponentWorker._evaluate_host(name, directory, components))
        return findings

    @staticmethod
    def check_components(strict=False, show_all=False):
        """Compare every browser application against the component sources beside it.

        Exits non-zero when a host serves bytes that are not the package it locks, creates an
        element no locked bundle defines, or pins a build the component's history cannot account
        for. These are what the release and train preflights gate on, because a clean install
        cannot repair any of them.

        Under --strict a host merely sitting behind a published component fails too. That is the
        question a server payload asks, since it serves whatever the lock resolves; a release does
        not ask it, because a host sits on the last published component for as long as it takes to
        publish the next one.
        """
        try:
            findings = ComponentWorker.findings()
        except ComponentGateError as error:
            console.print(f"[red]{str(error).capitalize()}.[/red]")
            return 1

        if not findings:
            console.print("No browser application pins a component in this workspace.")
            return 0

        failures = [finding for finding in findings if finding.is_failure]
        strict_failures = [finding for finding in findings
                           if finding.is_strict_failure and not finding.is_failure]
        ComponentWorker._report(findings, failures, strict_failures, strict, show_all)
        if failures:
            return 1
        return 1 if strict and strict_failures else 0

    # Reading the workspace

    @staticmethod
    def _component_index(root=None):
        """Every repository in the workspace that publishes an npm package, by package name.

        Read from the checkouts rather than from the repository registry, because a component is a
        component as soon as a sibling installs it, and the estate has published two that the
        registry does not yet name.
        """
        root = Path(root or Util.cedar_home)
        index = {}
        for manifest in sorted(root.glob("*/package.json")):
            directory = manifest.parent
            if not (directory / ".git").exists():
                continue
            package = ComponentWorker._read_json(manifest)
            name = package.get("name") if isinstance(package, dict) else None
            if isinstance(name, str) and name:
                index[unscoped(name)] = directory
        return index

    @staticmethod
    def _evaluate_host(host, directory, components):
        """Every finding for one repository, across its pins, its staged bundles and its elements."""
        package = ComponentWorker._read_json(directory / "package.json")
        pins = dependency_pins(package) if isinstance(package, dict) else {}
        locked = {name: directory / "node_modules" / unscoped(name) for name in pins}

        findings = [ComponentWorker._evaluate_pin(host, name, version, components)
                    for name, version in sorted(pins.items())
                    if unscoped(name) in components]
        findings.extend(ComponentWorker._evaluate_staged(host, directory))
        findings.extend(ComponentWorker._evaluate_elements(host, directory, locked))
        return findings

    @staticmethod
    def _evaluate_pin(host, name, version, components):
        """One host's pin on one component, measured against that component's own history."""
        component = unscoped(name)
        directory = components[component]
        commit = pinned_commit(version) or ComponentWorker._release_commit(directory, version)
        head = ComponentWorker._head(directory)
        if commit is None or head is None:
            return evaluate_pin(host, component, version, commit, head, (), True)
        reachable = ComponentWorker._is_ancestor(directory, commit)
        unseen = ComponentWorker._subjects(directory, commit) if reachable else ()
        return evaluate_pin(host, component, version, commit, head, unseen, reachable)

    @staticmethod
    def _evaluate_staged(host, directory):
        """Each bundle this host serves, against the package it locks."""
        manifest = ComponentWorker._read_json(directory / STAGED_MANIFEST)
        if not isinstance(manifest, dict):
            return []
        findings = []
        for name, entry in sorted(manifest.items()):
            if not isinstance(entry, dict):
                continue
            package_dir = directory / "node_modules" / unscoped(name)
            locked = ComponentWorker._read_json(package_dir / "package.json")
            locked_version = locked.get("version") if isinstance(locked, dict) else None
            findings.append(evaluate_bundle(
                host, unscoped(name), entry.get("source"), entry.get("sha256"),
                locked_version, ComponentWorker._sha256(package_dir / f"{unscoped(name)}.js")))
        return findings

    @staticmethod
    def _evaluate_elements(host, directory, locked):
        """Each component element this host's own code names, against the bundles it locks.

        Only a host that stages sibling bundles is asked. A component defines its own elements in
        its own source and installs no bundle that could define them, so measuring one against its
        locked packages would report every element it owns as missing.
        """
        if not (directory / STAGED_MANIFEST).is_file():
            return []
        referenced = set()
        for pattern in SOURCE_GLOBS:
            for source in directory.glob(pattern):
                if any(part in EXCLUDED_PARTS for part in source.parts):
                    continue
                referenced |= referenced_elements(ComponentWorker._read_text(source))
        if not referenced:
            return []
        bundles = {name: ComponentWorker._read_text(package_dir / f"{unscoped(name)}.js")
                   for name, package_dir in locked.items()}
        return [evaluate_element(host, element, next(
            (unscoped(name) for name, bundle in sorted(bundles.items()) if element in bundle), None))
            for element in sorted(referenced)]

    # Git

    @staticmethod
    def _git(directory, arguments):
        completed = subprocess.run(["git", "-C", str(directory)] + arguments,
                                   capture_output=True, text=True, check=False,
                                   env=invocation_environment())
        return completed.stdout.strip() if completed.returncode == 0 else None

    @staticmethod
    def _head(directory):
        """The develop head, which is the branch every component publishes from."""
        return ComponentWorker._git(directory, ["rev-parse", "--verify", "develop"])

    @staticmethod
    def _release_commit(directory, version):
        """The commit a plain release version was tagged at, for a host that pins a release."""
        tag = release_tag(version)
        return ComponentWorker._git(directory, ["rev-parse", "--verify", f"{tag}^{{commit}}"]) if tag else None

    @staticmethod
    def _is_ancestor(directory, commit):
        completed = subprocess.run(
            ["git", "-C", str(directory), "merge-base", "--is-ancestor", commit, "develop"],
            capture_output=True, text=True, check=False, env=invocation_environment())
        return completed.returncode == 0

    @staticmethod
    def _subjects(directory, commit):
        """The subjects of the commits on develop that this pin was built before."""
        log = ComponentWorker._git(directory, ["log", "--format=%s", f"{commit}..develop"])
        return tuple(line for line in (log or "").splitlines() if line)

    # Files

    @staticmethod
    def _read_json(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _read_text(path):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @staticmethod
    def _sha256(path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    # Reporting

    @staticmethod
    def _report(findings, failures, strict_failures, strict, show_all):
        table = Table(
            "Host",
            "Component",
            Column(header="Surface", justify="center"),
            Column(header="State", justify="center"),
            Column(header="Detail"),
        )
        shown = findings if show_all else [f for f in findings if f.state != ComponentState.CURRENT]
        for finding in shown:
            table.add_row(finding.host, finding.component, finding.surface.value,
                          STATE_ICON[finding.state], finding.detail)
            for subject in finding.unseen:
                table.add_row("", "", "", "", f"  [dim]unseen:[/dim] {subject}")

        current = len([f for f in findings if f.state == ComponentState.CURRENT])
        caption = f"{current}/{len(findings)} comparisons current"
        if failures:
            caption += f"\n[red]{len(failures)} serving or naming something the pins do not account for[/red]"
        if strict_failures:
            colour = "red" if strict else "yellow"
            caption += (f"\n[{colour}]{len(strict_failures)} behind a published component, "
                        f"overridden locally, or unresolvable[/{colour}]")
        table.caption = caption
        console.print(table)

        if failures:
            console.print(
                "\nA host that serves bytes its lock does not name, or creates an element no "
                "locked bundle defines, fails at the moment a person opens the surface that needs "
                "it, and on no machine but the one that staged it. Publish the component's current "
                "source, advance the host's pin to that snapshot, and stage the locked package.")
        elif strict_failures and strict:
            console.print(
                "\nPublish each component's current source and advance the pins onto it before "
                "building a server payload or starting a release.")

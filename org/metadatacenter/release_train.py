"""Release commands and compatibility exports for the release component API."""
from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
import fcntl
import dataclasses
import datetime as dt
import fnmatch
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text
from org.metadatacenter.github_ci import (
    GREEN_CONCLUSIONS,
    GithubCIProbeError,
    latest_runs_by_name,
    probe_exact_commit,
    run_url,
)
from org.metadatacenter.npm_policy import (
    npm_user_config_findings,
    unreviewed_install_scripts,
)
from org.metadatacenter import smoke_gate
from org.metadatacenter.util.NexusCredentials import CredentialError, environment_with_nexus_credentials
from org.metadatacenter.util.BuildTrain import BuildTrain
from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError,
    embedded_mongo_processes,
    require_no_embedded_mongo_processes,
    wait_for_no_embedded_mongo_processes,
)
from org.metadatacenter.util.SubprocessDiagnostics import describe_subprocess_failure

from org.metadatacenter.release_support.acceptance import (
    ReleaseAcceptance,
    _publication_evidence_by_plan,
)

from org.metadatacenter.release_support.distribution import (
    ReleaseDistributionMaterializer,
)

from org.metadatacenter.release_support.errors import (
    ReleaseError,
    RetryableReleaseError,
)

from org.metadatacenter.release_support.hashes import (
    _directory_file_hashes,
    _file_sha256,
    _json_bytes,
    _sha256,
)

from org.metadatacenter.release_support.integration import (
    ReleaseRemoteIntegrator,
)

from org.metadatacenter.release_support.lifecycle import (
    RELEASE_FINAL_PHASES,
    RELEASE_STAGES,
    RELEASE_TERMINAL_PHASE,
    REWIND_TO_FRONTENDS,
    ReleaseStage,
    TRANSIENT_RETRY_ATTEMPTS,
    TRANSIENT_RETRY_BACKOFF_SECONDS,
    _drive_release,
    _next_release_stage,
    _release_stage_has_finished,
    abandon_active_release,
    accept_active_release,
    advance_active_release,
    create_active_release_refs,
    integrate_active_release,
    prepare_active_release,
    prepare_active_release_versions,
    publish_active_release,
    publish_active_release_snapshots,
    validate_active_release_builds,
)

from org.metadatacenter.release_support.output import (
    console,
)

from org.metadatacenter.release_support.packages import (
    _canonicalize_minified_renames,
    _normalize_bundle_provenance,
    _normalize_package_metadata,
    _normalized_bundle_manifest,
    _one_match,
    _public_release_changelog,
    _read_package_json,
    _replace_once,
    _tarball_files,
    _tree_digest,
    _verify_bundle,
    _verify_integrity,
    compare_cee_packages,
)

from org.metadatacenter.release_support.planning import (
    ReleasePlanner,
)

from org.metadatacenter.release_support.policy import (
    ABANDONABLE_RELEASE_PHASES,
    ACCEPT_MAIN_ONLY_HELP,
    ACCEPT_RED_DEVELOP_HELP,
    CHECKOUT_BYTES_PER_REPOSITORY,
    DEV_CEE_NAME,
    DEV_MODEL_SPEC_RE,
    FRONTEND_BUILD_SURFACES,
    FRONTEND_BYTES_PER_SURFACE_VARIANT,
    GENERATED_VERSION_FILE_GLOBS,
    GIT_SHA_RE,
    INDEPENDENT_RELEASE_REPOSITORIES,
    LICENSE_COPYRIGHT_RE,
    LICENSE_FILE_NAME,
    LINUX_JVM_ROOT,
    LOAD_TRACE_RE,
    MAVEN_BYTES_PER_REPOSITORY_VARIANT,
    MAVEN_GENERATED_VERSION_FILES,
    MAVEN_RELEASE_REPOSITORY,
    MAVEN_SNAPSHOT_REPOSITORY,
    MINIFIED_NAME_RE,
    MINIFIED_TOKEN_SPLIT,
    MINIMUM_SPACE_HEADROOM_BYTES,
    NEXT_VERSION_RE,
    NEXUS_AUTHENTICATED_ENDPOINT,
    NEXUS_HOST,
    NEXUS_NPM_REGISTRY,
    NEXUS_REPOSITORY_PROBE,
    NEXUS_WRITABLE_ENDPOINT,
    NODE_24_CANDIDATE_DIRECTORIES,
    NPM_RELEASE_SURFACES,
    NPM_VERSION_SURFACES,
    PROFILE_COMMAND,
    PROFILE_REQUIRED_VARIABLES,
    PUBLICATION_CACHE_AND_LOG_BYTES,
    PUBLIC_CEE_NAME,
    PUBLIC_NPM_REGISTRY,
    REQUIRED_CEE_FILES,
    REQUIRED_JAVA_MAJOR,
    REQUIRED_NODE_VERSION,
    REQUIRED_TOOLS,
    RESERVED_SHORT_WORDS,
    RETRYABLE_TRANSPORT_TEXT,
    SHA256_RE,
    SPACE_HEADROOM_PERCENT,
    STABLE_VERSION_RE,
    _integration_repositories,
    _stable_version_key,
    _validate_stable_version,
)

from org.metadatacenter.release_support.preflight import (
    PreflightFinding,
    ReleasePreflight,
    ReleaseSpaceBudget,
    ReleaseSpaceEstimator,
)

from org.metadatacenter.release_support.presentation import (
    _ACTIVE_RELEASE_SECTIONS,
    _publication_progress,
    _release_progress,
    _release_watch_summary,
    _render_plan,
    _render_preflight_findings,
    _render_release_status,
    _watch_release,
)

from org.metadatacenter.release_support.publication import (
    ReleaseArtifactPublisher,
)

from org.metadatacenter.release_support.refs import (
    ReleaseRefCreator,
)

from org.metadatacenter.release_support.state import (
    ReleaseState,
)

from org.metadatacenter.release_support.toolchain import (
    ToolchainResolver,
    java_17_remediation,
    node_24_remediation,
)

from org.metadatacenter.release_support.transport import (
    HttpClient,
    NexusCircuitBreaker,
    TrainState,
    _command_failure_is_retryable,
    _environment_with_nexus_credentials,
    _raise_command_failure,
)

from org.metadatacenter.release_support.validation import (
    ReleaseBuildValidator,
)

from org.metadatacenter.release_support.versioning import (
    ReleaseVersionPreparer,
)

from org.metadatacenter.release_support.workspace import (
    ReleaseWorkspacePreparer,
)

app = typer.Typer()


def _activate_toolchain() -> None:
    """Give this process the release's Java and Node before any check or build asks for them."""
    for note in ToolchainResolver(os.environ).resolve():
        console.print(f"Toolchain:           {note}")


def _build_or_exit(
    release_version: str,
    next_version: str,
    from_train: str,
    cee_version: str,
) -> dict:
    try:
        return ReleasePlanner().build(
            release_version=release_version,
            next_version=next_version,
            train=from_train,
            cee_version=cee_version,
        )
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error


def _parse_accepted_red_develop(values: list[str] | None) -> dict[str, str]:
    accepted = {}
    for value in values or []:
        repository, separator, run_id = value.partition("=")
        if not separator or not repository.strip() or not run_id.strip():
            console.print(
                f"[red]--accept-red-develop expects <repository>=<run-id>, not {value!r}[/red]")
            raise typer.Exit(1)
        accepted[repository.strip()] = run_id.strip()
    return accepted


def _release_gate_or_exit(
    manifest: dict,
    accepted_red_develop: dict[str, str],
    accepted_main_only: set[str] | None = None,
) -> None:
    """Report every settled precondition, and stop before any state changes if one failed."""
    try:
        findings = ReleasePreflight(
            manifest,
            accepted_red_develop=accepted_red_develop,
            accepted_main_only=accepted_main_only,
        ).run()
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    _render_preflight_findings(findings)


def _release_resume_gate_or_exit(manifest: dict) -> None:
    try:
        findings = ReleasePreflight(manifest).run_resume()
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    _render_preflight_findings(findings)


@app.command("plan")
def plan(
    release_version: str = typer.Option(..., "--version", help="Explicit CEDAR release version"),
    next_version: str = typer.Option(..., "--next-version", help="Explicit next SNAPSHOT version"),
    from_train: str = typer.Option(..., "--from-train", help="Completed development build train"),
    cee_version: str = typer.Option(..., "--cee-version", help="Exact public npmjs CEE version"),
    accept_red_develop: list[str] = typer.Option(
        None, "--accept-red-develop", help=ACCEPT_RED_DEVELOP_HELP),
    accept_main_only: list[str] = typer.Option(
        None, "--accept-main-only", help=ACCEPT_MAIN_ONLY_HELP),
):
    """Settle every release precondition without changing release state."""
    _activate_toolchain()
    manifest = _build_or_exit(release_version, next_version, from_train, cee_version)
    _render_plan(manifest)
    _release_gate_or_exit(
        manifest,
        _parse_accepted_red_develop(accept_red_develop),
        {value.strip() for value in (accept_main_only or []) if value.strip()},
    )
    console.print("No changes made.")


@app.command("start")
def start(
    release_version: str = typer.Option(..., "--version", help="Explicit CEDAR release version"),
    next_version: str = typer.Option(..., "--next-version", help="Explicit next SNAPSHOT version"),
    from_train: str = typer.Option(..., "--from-train", help="Completed development build train"),
    cee_version: str = typer.Option(..., "--cee-version", help="Exact public npmjs CEE version"),
    accept_red_develop: list[str] = typer.Option(
        None, "--accept-red-develop", help=ACCEPT_RED_DEVELOP_HELP),
    accept_main_only: list[str] = typer.Option(
        None, "--accept-main-only", help=ACCEPT_MAIN_ONLY_HELP),
    verbose: bool = typer.Option(
        False, "--verbose", help="Stream full task output instead of compact progress"),
):
    """Run a manifest-owned train release through verified Git and publication stages."""
    state = ReleaseState()
    try:
        with state.exclusive():
            _activate_toolchain()
            manifest = _build_or_exit(release_version, next_version, from_train, cee_version)
            _render_plan(manifest)
            _release_gate_or_exit(
                manifest,
                _parse_accepted_red_develop(accept_red_develop),
                {value.strip() for value in (accept_main_only or []) if value.strip()},
            )
            path = state.start(manifest)
            console.print("Compact progress is shown below; full task output is retained in attempt logs.")
            console.print("A second terminal may run: cedarcli release status --watch")
            active = _drive_release(state, verbose=verbose)
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        if state.current_path.exists():
            console.print("Release state and the failed attempt were retained; use cedarcli release resume.")
        raise typer.Exit(1) from error
    console.print(f"Phase:               {active['phase']}")
    console.print(f"Internal state:      {path}")


@app.command("resume")
def resume(
    verbose: bool = typer.Option(
        False, "--verbose", help="Stream full task output instead of compact progress"),
):
    """Resume the active train-backed release from its recorded phase."""
    state = ReleaseState()
    try:
        with state.exclusive():
            _activate_toolchain()
            active, path = state.read_current_manifest()
            _release_resume_gate_or_exit(active)
            console.print("Compact progress is shown below; full task output is retained in attempt logs.")
            console.print("A second terminal may run: cedarcli release status --watch")
            manifest = _drive_release(state, verbose=verbose)
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    _render_plan(manifest)
    console.print(f"Phase:               {manifest['phase']}")
    console.print(f"Internal state:      {path}")


@app.command("abandon")
def abandon(
    release_version: str = typer.Option(
        ..., "--version", help="Exact active release version to abandon"),
    reason: str = typer.Option(
        ..., "--reason", help="Why this local-only attempt cannot be resumed"),
):
    """Retain and close an attempt that has not begun external publication."""
    state = ReleaseState()
    try:
        with state.exclusive():
            manifest, path = abandon_active_release(release_version, reason, state)
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    console.print(f"Abandoned release:   {manifest['releaseVersion']}")
    console.print(f"Previous phase:      {manifest['abandonment']['previousPhase']}")
    console.print(f"Reason:              {manifest['abandonment']['reason']}")
    console.print(f"Retained state:      {path}")


@app.command("status")
def status(
    watch: bool = typer.Option(False, "--watch", help="Watch compact progress until release stops"),
):
    """Show the active train-backed release and its immutable CEE proof."""
    try:
        state = ReleaseState()
        manifest, path = state.read_current_manifest()
    except ReleaseError as error:
        console.print(f"[red]{error}[/red]")
        raise typer.Exit(1) from error
    _render_plan(manifest)
    if watch:
        try:
            manifest, path, code = _watch_release(state)
        except KeyboardInterrupt:
            console.print("[yellow]Stopped watching; the release state is unchanged.[/yellow]")
            raise typer.Exit(130)
        if manifest.get("acceptance"):
            for check in manifest["acceptance"]["checks"]:
                console.print(f"Accepted:            {check['detail']}")
        _render_release_status(manifest, path)
        if code:
            raise typer.Exit(code)
        return
    if manifest.get("acceptance"):
        for check in manifest["acceptance"]["checks"]:
            console.print(f"Accepted:            {check['detail']}")
    _render_release_status(manifest, path)

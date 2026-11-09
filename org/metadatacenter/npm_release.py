"""`cedarcli release npm`: publish the model library and CEE to public npmjs."""
from __future__ import annotations

from pathlib import Path

import typer

from org.metadatacenter.npm_policy import STRICT_INSTALL_ENVIRONMENT
from org.metadatacenter.npm_release_support.definitions import DEV_MODEL_VERSION, PACKAGES
from org.metadatacenter.npm_release_support.errors import NpmReleaseError, NpmReleaseWait
from org.metadatacenter.npm_release_support.preflight import NpmReleasePreflight, run_identifier
from org.metadatacenter.npm_release_support.requests import parse_run_request
from org.metadatacenter.npm_release_support.route import ABANDONABLE_BEFORE, PACKAGE_STAGES, NpmReleaseRoute
from org.metadatacenter.npm_release_support.state import NpmReleaseState, now
from org.metadatacenter.npm_release_support.tools import Tools
from org.metadatacenter.release_support.output import console
from org.metadatacenter.release_support.state import ReleaseState
from org.metadatacenter.release_support.toolchain import ToolchainResolver
from org.metadatacenter.util.InvocationContext import invocation_environment

app = typer.Typer(no_args_is_help=True)

PACKAGES_HELP = "Packages to release, as <package>@<version>: model or cee"
NEXT_HELP = "Each package's next development base, as <package>@<version>"
MODEL_HELP = "Public model version CEE pins, when the model is not released in the run"
DEV_MODEL_HELP = "Development model version develop returns to, instead of the one it pinned before"
NOTES_HELP = "Markdown bullets for a package's changelog entry and release draft, as <package>=<file>"
DEFER_HELP = "Leave the lock baseline refresh in cedar-development to the operator"


def _say(message: str) -> None:
    console.print(message, markup=False, soft_wrap=True)


def _context() -> tuple[Path, Tools]:
    environment = dict(invocation_environment())
    home = environment.get("CEDAR_HOME")
    if not home:
        _say("CEDAR_HOME is not set")
        raise typer.Exit(1)
    for note in ToolchainResolver(environment).resolve():
        _say(f"Toolchain: {note}")
    # Every frontend CI install is strict about install scripts; a release gate is no looser.
    environment.update(STRICT_INSTALL_ENVIRONMENT)
    return Path(home), Tools(environment, reporter=_say)


def _platform_release_active() -> bool:
    try:
        current = ReleaseState().read_current()
    except Exception:
        return False
    return not current.get("concludedAt")


def _refresh_baselines(repositories: list[str]) -> int:
    from org.metadatacenter.worker.LockBaselineWorker import LockBaselineWorker
    return LockBaselineWorker.refresh(repositories=repositories)


def _route(home: Path, tools: Tools, state: NpmReleaseState) -> NpmReleaseRoute:
    return NpmReleaseRoute(home, tools, state, reporter=_say, baseline_refresher=_refresh_baselines,
                           platform_release_active=_platform_release_active)


def _render_plan(packages: list[dict]) -> None:
    for record in packages:
        definition = PACKAGES[record["package"]]
        pins = f", pinning model {record['modelVersion']}" if record.get("modelVersion") else ""
        _say(f"{definition.public_name}@{record['version']}{pins}; develop then advances to "
             f"{record['nextBase']}-dev")


def _preflight(specs, nexts, model_version, development_model_version, notes, defer_baselines):
    try:
        request = parse_run_request(specs, nexts, model_version, development_model_version, notes, defer_baselines)
    except NpmReleaseError as error:
        _say(str(error))
        raise typer.Exit(1) from error
    home, tools = _context()
    try:
        findings, records = NpmReleasePreflight(home, tools).run(request)
    except NpmReleaseError as error:
        _say(str(error))
        raise typer.Exit(1) from error
    _render_plan(records)
    if findings:
        _say(f"{len(findings)} precondition(s) refuse this release:")
        for finding in findings:
            _say(f"  - {finding}")
        raise typer.Exit(1)
    _say("Every precondition holds.")
    return home, tools, request, records


def _render_next_steps(run: dict) -> None:
    _say("Released:")
    for record in run["packages"]:
        definition = PACKAGES[record["package"]]
        _say(f"  {definition.public_name}@{record['version']}, tagged {definition.tag(record['version'])}")
        draft = (record.get("evidence", {}).get("notes") or {}).get("draft")
        if draft:
            _say(f"  Review and publish the release draft: {draft}")
        elif record["package"] == "model":
            _say("  Write the model's release notes as the GitHub release on its tag.")
    if any(record["package"] == "cee" for record in run["packages"]):
        version = next(record["version"] for record in run["packages"] if record["package"] == "cee")
        _say("Publishing is not deployment. Pin the public CEE in its consumers with:")
        _say(f"  node $CEDAR_HOME/cedar-development/ops/propagate-cee-release.mjs --apply {version}")
    _say("Once CI has published each advanced develop head: cedarcli publish components --apply")
    baselines = (run.get("evidence") or {}).get("baselines") or {}
    if baselines.get("deferred"):
        _say("Refresh and commit the lock baselines: cedarcli publish baselines --refresh "
             + " ".join(f"--repository {name}" for name in baselines["repositories"]))


def _drive(route: NpmReleaseRoute, state: NpmReleaseState, run: dict) -> None:
    try:
        finished = route.drive(run)
    except NpmReleaseWait as wait:
        _say(f"Waiting: {wait}")
        _say("The run is recorded; continue it with cedarcli release npm resume.")
        raise typer.Exit(2) from wait
    except NpmReleaseError as error:
        _say(f"Stopped: {error}")
        _say(f"The run is recorded in {state.run_path(run['id'])}; fix the cause and run "
             "cedarcli release npm resume.")
        raise typer.Exit(1) from error
    _render_next_steps(finished)


@app.command("plan")
def plan(
    packages: list[str] = typer.Argument(..., help=PACKAGES_HELP),
    next_base: list[str] = typer.Option(None, "--next", help=NEXT_HELP),
    model_version: str = typer.Option(None, "--model-version", help=MODEL_HELP),
    development_model_version: str = typer.Option(None, "--dev-model-version", help=DEV_MODEL_HELP),
    notes: list[str] = typer.Option(None, "--notes", help=NOTES_HELP),
    defer_baselines: bool = typer.Option(False, "--defer-baselines", help=DEFER_HELP),
):
    """Settle every precondition of an npm release without changing anything."""
    _preflight(packages, next_base, model_version, development_model_version, notes, defer_baselines)
    _say("No changes made.")


@app.command("start")
def start(
    packages: list[str] = typer.Argument(..., help=PACKAGES_HELP),
    next_base: list[str] = typer.Option(None, "--next", help=NEXT_HELP),
    model_version: str = typer.Option(None, "--model-version", help=MODEL_HELP),
    development_model_version: str = typer.Option(None, "--dev-model-version", help=DEV_MODEL_HELP),
    notes: list[str] = typer.Option(None, "--notes", help=NOTES_HELP),
    defer_baselines: bool = typer.Option(False, "--defer-baselines", help=DEFER_HELP),
):
    """Release packages to npmjs through a recorded, resumable route."""
    state = NpmReleaseState()
    try:
        with state.exclusive():
            home, tools, request, records = _preflight(
                packages, next_base, model_version, development_model_version, notes, defer_baselines)
            run = {"id": run_identifier(request), "packages": records, "completed": [], "evidence": {},
                   "deferBaselines": request.defer_baselines, "stage": None}
            state.start(run)
            _drive(_route(home, tools, state), state, run)
    except NpmReleaseError as error:
        _say(str(error))
        raise typer.Exit(1) from error


@app.command("resume")
def resume(
    development_model_version: str = typer.Option(None, "--dev-model-version", help=DEV_MODEL_HELP),
):
    """Continue the active npm release from the stage it stopped at."""
    state = NpmReleaseState()
    try:
        with state.exclusive():
            run, _ = state.read()
            if development_model_version:
                if not DEV_MODEL_VERSION.fullmatch(development_model_version):
                    raise NpmReleaseError(f"{development_model_version!r} is not a model development version")
                for record in run["packages"]:
                    if PACKAGES[record["package"]].consumes_model and "develop" not in record["completed"]:
                        record["developmentModelVersion"] = development_model_version
                state.save(run)
            home, tools = _context()
            _drive(_route(home, tools, state), state, run)
    except NpmReleaseError as error:
        _say(str(error))
        raise typer.Exit(1) from error


@app.command("status")
def status():
    """Show the active or most recent npm release and the evidence each stage recorded."""
    state = NpmReleaseState()
    try:
        run, path = state.read(include_concluded=True)
    except NpmReleaseError as error:
        _say(str(error))
        raise typer.Exit(1) from error
    _say(f"Run:      {run['id']}")
    _say(f"Ledger:   {path}")
    if run.get("conclusion"):
        _say(f"Outcome:  {run['conclusion']} at {run['concludedAt']}")
    for record in run["packages"]:
        definition = PACKAGES[record["package"]]
        done = len(record["completed"])
        nxt = next((stage for stage in PACKAGE_STAGES if stage not in record["completed"]), None)
        _say(f"{definition.public_name}@{record['version']}: {done}/{len(PACKAGE_STAGES)} stages"
             + (f", next {nxt}" if nxt else ", complete"))
    for key, label in (("failure", "Stopped"), ("waiting", "Waiting")):
        if run.get(key):
            item = run[key]
            _say(f"{label}:  {item['package']} {item['stage']}: {item['detail']}")


@app.command("abandon")
def abandon(reason: str = typer.Option(..., "--reason", help="Why this run cannot be resumed")):
    """Close a run before any package has merged to main; what it pushed is listed for cleanup."""
    state = NpmReleaseState()
    try:
        with state.exclusive():
            run, path = state.read()
            merged = [record["package"] for record in run["packages"] if ABANDONABLE_BEFORE in record["completed"]]
            if merged:
                raise NpmReleaseError(
                    f"{', '.join(merged)} already merged to main; resume the run rather than abandon it")
            run["abandonment"] = {"reason": reason, "at": now()}
            state.conclude(run, "abandoned")
    except NpmReleaseError as error:
        _say(str(error))
        raise typer.Exit(1) from error
    _say(f"Abandoned {run['id']}; the ledger is kept at {path}.")
    for record in run["packages"]:
        definition = PACKAGES[record["package"]]
        if "push" in record["completed"]:
            _say(f"  {definition.repository} develop holds the preparation commit "
                 f"{record['evidence']['commit']['commit'][:8]}; revert it on develop")
        if "pull-request" in record["completed"]:
            _say(f"  close {record['evidence']['pull-request']['url']}")

"""What an accepted CEDAR release leaves to do on this workstation, as one command.

An accepted release advances `develop` everywhere and leaves the checkouts on the commits before it,
the consumers on the packages it pinned, the lock baselines stale, the services on jars of the
version it released, and the frontends unbuilt at the next one. Each of those has a command of its
own. After release 2.9.35 they took about an hour and two dozen steps by hand, in an order that
matters: the baselines follow the pin moves, and the reactor's smoke runs last because any commit
before it would leave the smoke covering an older head.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from org.metadatacenter.release_support.errors import ReleaseError

BASELINES = "ops/frontend-train.json"


@dataclass(frozen=True)
class FollowThroughStep:
    name: str
    summary: str
    arguments: tuple[str, ...] = ()
    # A step whose failure is reported without stopping the steps after it.
    advisory: bool = False


def follow_through_steps(jobs: int | None = None, workers: int | None = None) -> tuple:
    frontends = ("build",)
    if jobs:
        frontends += ("--jobs", str(jobs))
    if workers:
        frontends += ("--workers", str(workers))
    return (
        FollowThroughStep("pull", "fast-forward every checkout to the develop the release advanced",
                          ("git", "pull")),
        FollowThroughStep("pins", "return every consumer to the development packages, committed and pushed",
                          ("publish", "components", "--apply", "--push", "--settle")),
        FollowThroughStep("baselines", "refresh the npm lock baselines the pin moves changed, "
                          "then commit and push them"),
        FollowThroughStep("contracts", "dispatch the design-token contracts the pin pushes left stale",
                          ("check", "ci", "--dispatch-stale-contracts"), advisory=True),
        FollowThroughStep("java", "build every Java repository at the next development version",
                          ("build", "java")),
        FollowThroughStep("services", "restart the microservices on the jars just built",
                          ("native", "restart", "microservices")),
        FollowThroughStep("frontends", "build, redeploy and smoke the frontends",
                          frontends + ("frontends",)),
    )


def run_follow_through(cedar_home: Path, release_version: str, steps, *, start: str | None = None,
                       run=None, git=None, say=print) -> int:
    """Run each step from `start` on, stopping at the first that fails."""
    names = [step.name for step in steps]
    if start is not None and start not in names:
        raise ReleaseError(f"no follow-through step is called {start!r}; the steps are "
                           + ", ".join(names))
    run = run or _cli_runner(cedar_home)
    git = git or _git_runner
    selected = steps[names.index(start):] if start else steps
    for step in selected:
        say(f"Follow-through: {step.name}, {step.summary}")
        if step.name == "baselines":
            code = _refresh_baselines(cedar_home, release_version, run, git, say)
        else:
            code = run(list(step.arguments))
        if code and step.advisory:
            say(f"Follow-through: {step.name} reported findings; the steps after it continue")
        elif code:
            say(f"Follow-through stopped at {step.name}. Repair it, then run "
                f"cedarcli release follow-through --from {step.name}")
            return code
    say(f"Follow-through for {release_version} is complete")
    return 0


def _refresh_baselines(cedar_home: Path, release_version: str, run, git, say) -> int:
    """Refresh the baselines, and commit them only when no advisory count moved.

    The commit takes the one file and nothing else, so a file someone else is editing is refused
    before the refresh writes to it. A moved advisory count needs a dependency review, which is
    not this command's to give.
    """
    development = Path(cedar_home) / "cedar-development"
    if git(development, "status", "--porcelain=v1", "--", BASELINES):
        say(f"cedar-development {BASELINES} holds uncommitted changes that are not this command's; "
            "commit or set them aside first")
        return 1
    code, output = run(["publish", "baselines", "--refresh"], capture=True)
    if code:
        return code
    moved = baseline_count_changes(output)
    if moved:
        say("Advisory counts moved, which needs a dependency review before the refresh is committed: "
            + "; ".join(moved))
        return 1
    if not git(development, "status", "--porcelain=v1", "--", BASELINES):
        say("No lock baseline changed")
        return 0
    message = (f"Refresh the npm lock baselines after CEDAR {release_version}\n\n"
               "Returning the consumers to the development packages that follow the release\n"
               "rewrote these locks. Each digest is refreshed, and no advisory count changed.\n")
    git(development, "commit", "--quiet", "-m", message, "--", BASELINES)
    git(development, "push", "--quiet", "origin", "develop")
    return 0


def baseline_count_changes(output: str) -> list[str]:
    """Each refreshed lock whose advisory counts differ, as `publish baselines` printed it."""
    changes = []
    for line in output.splitlines():
        match = re.search(r"^(\S+): digest .* counts (\S+) -> (\S+)$", line.strip())
        if match and match.group(2) != match.group(3):
            changes.append(f"{match.group(1)} {match.group(2)} -> {match.group(3)}")
    return changes


def _cli_runner(cedar_home: Path):
    cli = Path(cedar_home) / "cedar-cli" / "cli.sh"

    def run(arguments, capture=False):
        completed = subprocess.run(["bash", str(cli), *arguments], cwd=str(cedar_home), check=False,
                                   text=True, capture_output=capture)
        if capture:
            print(completed.stdout, end="")
            return completed.returncode, completed.stdout
        return completed.returncode
    return run


def _git_runner(directory: Path, *arguments: str) -> str:
    completed = subprocess.run(["git", "-C", str(directory), *arguments], capture_output=True,
                               text=True, check=False)
    if completed.returncode:
        raise ReleaseError(f"git {' '.join(arguments[:2])} failed in {Path(directory).name}: "
                           f"{(completed.stderr or completed.stdout).strip()}")
    return completed.stdout.strip()

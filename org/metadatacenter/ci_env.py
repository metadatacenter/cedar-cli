"""The CI environment block each Java repository copies, and whether the copies still agree.

A CEDAR server declares in code what its configuration needs, and a variable it needs but does not
get stops a suite at the first CedarConfig.getInstance. Every Java repository's ci.yml therefore
carries a copy of one block, `cedar-development/ops/ci-env-block.yml`, and `ops/check_ci_env.py`
compares each copy against it and against what the code asks for.

That script had no command in front of it and ran in no gate. It was written to prevent exactly the
failure it then failed to prevent: on 2026-09-11 seventeen copies were missing the artifact service
credential, and the first repository whose suite asked for it went red on the first CI run after the
entry appeared, two days later.
"""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util
from pathlib import Path
from rich.console import Console
import subprocess
import sys

console = Console()

SCRIPT = Path("cedar-development") / "ops" / "check_ci_env.py"


def _script(cedar_home=None) -> Path:
    home = cedar_home or Util.cedar_home or invocation_environment().get("CEDAR_HOME")
    if not home:
        raise ValueError("CEDAR_HOME is not set")
    return Path(home) / SCRIPT


def ci_env_report(apply: bool = False, cedar_home=None, runner=subprocess.run):
    """Run the drift check and return its exit code with everything it printed.

    Raises ValueError when the check cannot run at all, which a caller that only wants an
    advisory can report without treating it as drift.
    """
    script = _script(cedar_home)
    if not script.is_file():
        raise ValueError(f"{script} is missing; cedar-development must be checked out")
    command = [sys.executable, str(script)] + (["--apply"] if apply else [])
    try:
        result = runner(command, text=True, capture_output=True, env=invocation_environment())
    except OSError as error:
        raise ValueError(f"cannot run the CI environment check: {error}") from error
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output


def check_ci_env(apply: bool = False, cedar_home=None, runner=subprocess.run) -> int:
    """Report drift, or repair it under --apply. Non-zero when a copy or the block has drifted."""
    try:
        code, output = ci_env_report(apply=apply, cedar_home=cedar_home, runner=runner)
    except ValueError as error:
        console.print(f"[red]{error}[/red]")
        return 1
    for line in output.splitlines():
        console.print(f"  {line}", markup=False)
    if code and not apply:
        console.print(
            "Rewrite the drifted copies with `cedarcli check ci-env --apply`, then commit each "
            "repository.")
    return code

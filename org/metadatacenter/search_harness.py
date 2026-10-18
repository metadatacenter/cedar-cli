"""Opt-in search acceptance matrices; default invocation is an offline plan."""
from pathlib import Path
import subprocess
import uuid

from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util


def run_search(*, plan=False, check=False, validate=False, live=False,
               isolated=False, tier="rest", report=None):
    if tier not in ("rest", "journey", "all"):
        raise ValueError("tier must be rest, journey or all")
    if sum((plan, check, validate, live)) > 1:
        raise ValueError("Choose one of --plan, --check, --validate or --live")
    home = Path(Util.cedar_home).resolve()
    root = home / "cedar-development/ops/e2e"
    command = ["node", str(root / "rest/search/runner.mjs"), f"--tier={tier}"]
    environment = dict(invocation_environment())
    if live:
        command.append("--live")
        if report is None:
            report = home / ".cedar/reports/search" / f"{uuid.uuid4()}.json"
    elif check:
        command.append("--check")
    elif validate:
        command.append("--validate")
    else:
        command.append("--plan")
    if isolated:
        command.append("--isolated")
    if report is not None:
        report = Path(report)
        if not report.is_absolute():
            report = Path(environment.get("CEDAR_CLI_CALLER_DIR", Path.cwd())) / report
        command.append(f"--report={report.resolve()}")
    return subprocess.run(command, cwd=root, env=environment).returncode

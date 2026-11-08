"""Opt-in, reporting-only frontend laboratory; independent of release/smoke gates."""
from pathlib import Path
import subprocess

from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.Util import Util
from org.metadatacenter.util.NodeBuildCheck import require_build_node


def run_performance(*, samples=5, profiles="normal,constrained", scenarios=None,
                    output=None, plan=False, check=False, deployment_manifest=None):
    if plan and check:
        raise ValueError("Choose --plan or --check")
    if type(samples) is not int or not 1 <= samples <= 30:
        raise ValueError("samples must be an integer from 1 to 30")
    if any(name not in ("normal", "constrained") for name in profiles.split(",")):
        raise ValueError("profiles must be normal and/or constrained")
    root = Path(Util.cedar_home).resolve() / "cedar-development/ops/e2e"
    environment = dict(invocation_environment())
    if not plan:
        require_build_node(Path(Util.cedar_home) / "cedar-workspace", environment)
    command = ["node", str(root / "frontend-perf/run.mjs"), f"--samples={samples}", f"--profiles={profiles}"]
    if scenarios:
        command.append(f"--scenarios={scenarios}")
    if output is not None:
        output = Path(output)
        if not output.is_absolute():
            output = Path(environment.get("CEDAR_CLI_CALLER_DIR", Path.cwd())) / output
        command.append(f"--output={output.resolve()}")
    if deployment_manifest is not None:
        manifest = Path(deployment_manifest)
        caller = Path(environment.get("CEDAR_CLI_CALLER_DIR", Path.cwd()))
        command.append(f"--deployment-manifest={(manifest if manifest.is_absolute() else caller / manifest).resolve()}")
    if plan:
        command.append("--plan")
    if check:
        command.append("--check")
    return subprocess.run(command, cwd=root, env=environment).returncode


def run_paired(*, component, baseline, candidate, output, samples=5):
    if component not in ("cee", "ced", "designer"):
        raise ValueError("component must be cee, ced or designer")
    if type(samples) is not int or not 1 <= samples <= 30:
        raise ValueError("samples must be an integer from 1 to 30")
    root = Path(Util.cedar_home).resolve() / "cedar-development/ops/e2e"
    environment = dict(invocation_environment())
    require_build_node(Path(Util.cedar_home) / "cedar-workspace", environment)
    caller = Path(environment.get("CEDAR_CLI_CALLER_DIR", Path.cwd()))
    command = ["node", str(root / "frontend-perf/isolated.mjs"), f"--component={component}", f"--samples={samples}"]
    for name, value in (("baseline", baseline), ("candidate", candidate), ("output", output)):
        path = Path(value)
        command.append(f"--{name}={(path if path.is_absolute() else caller / path).resolve()}")
    return subprocess.run(command, cwd=root, env=environment).returncode

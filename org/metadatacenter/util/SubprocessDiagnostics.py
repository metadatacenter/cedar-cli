import platform
import signal
from pathlib import Path


def describe_return_code(return_code: int) -> str:
    """Turn Python's negative signal return codes into an operator-facing result."""
    if return_code >= 0:
        return f"exited with code {return_code}"
    number = -return_code
    try:
        name = signal.Signals(number).name
    except ValueError:
        name = "UNKNOWN"
    return f"was terminated by {name} (signal {number})"


def crash_evidence_hint(return_code: int) -> str:
    if return_code >= 0:
        return ""
    system = platform.system()
    if system == "Darwin":
        location = Path.home() / "Library" / "Logs" / "DiagnosticReports"
        return f"; inspect macOS crash reports in {location}"
    if system == "Linux":
        return "; inspect coredumpctl and /var/lib/systemd/coredump for crash evidence"
    return "; inspect the platform crash-report or core-dump location for evidence"


def describe_subprocess_failure(return_code: int) -> str:
    return describe_return_code(return_code) + crash_evidence_hint(return_code)

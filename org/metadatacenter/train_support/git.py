"""CEDAR train git."""
from __future__ import annotations
from org.metadatacenter.util.InvocationContext import invocation_environment
import subprocess


def _git(root, *arguments):
    completed = subprocess.run(
        ['git', '-C', str(root), *arguments],
        capture_output=True, text=True, check=False, env=invocation_environment())
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()

"""CEDAR train git."""
from __future__ import annotations
import subprocess


def _git(root, *arguments):
    completed = subprocess.run(
        ['git', '-C', str(root), *arguments],
        capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()

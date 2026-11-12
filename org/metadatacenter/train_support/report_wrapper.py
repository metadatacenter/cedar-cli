"""Whether the train's Playwright report wrapper loads the tests each package's own config loads.

The train runs CEE's visual suite and CED's browser suite through a wrapper config it generates, so
it can add a JSON reporter, rather than through the config each package's CI uses. Train
2.9.35-dev.20261112.0646 lost all four CEE visual shards to that difference: the wrapper was
CommonJS, CEE's suite is an ES module, and a module the config and the specs share lost its named
exports, so no test ran. The package's own CI passed throughout. Listing both ways takes seconds.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# The lanes the train runs through its wrapper, by repository and the directory holding the config.
LANES = (("cedar-embeddable-editor", "visual"), ("cedar-embeddable-designer", "browser"))

WRAPPER = "train-report.config.ts"
REPORT = "train-report-rehearsal.json"
TOTAL = re.compile(r"^Total: (\d+) tests? in \d+ files?", re.MULTILINE)

# Writes the wrapper with the train's own function, so the rehearsal cannot drift from the train.
WRITE_WRAPPER = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                 "import frontend_train_shards as shards; "
                 "shards.report_config(Path(sys.argv[2]), sys.argv[3])")


def wrapper_findings(cedar_home, run=subprocess.run) -> list[str]:
    """One line for each lane whose wrapper lists other tests than its own config, or none."""
    home = Path(cedar_home)
    ops = home / "cedar-development" / "ops"
    findings = []
    for repository, lane in LANES:
        directory = home / repository / lane
        # Without an install there is nothing to list; the train installs its own.
        if not (directory / "node_modules").is_dir() or not (directory / "playwright.config.ts").is_file():
            continue
        wrapper, report = directory / WRAPPER, directory / REPORT
        if wrapper.exists():
            findings.append(f"{repository}/{lane}/{WRAPPER} already exists, so the train's wrapper "
                            "cannot be rehearsed there; remove it")
            continue
        try:
            written = run([sys.executable, "-c", WRITE_WRAPPER, str(ops), str(directory), REPORT],
                          capture_output=True, text=True, check=False)
            if written.returncode:
                findings.append(f"the train cannot write its Playwright report wrapper for "
                                f"{repository} {lane}: {(written.stderr or written.stdout).strip()}")
                continue
            own = _listed(run, directory, [])
            wrapped = _listed(run, directory, [f"--config={WRAPPER}"])
        finally:
            wrapper.unlink(missing_ok=True)
            report.unlink(missing_ok=True)
        if own is not None and wrapped != own:
            findings.append(
                f"the train's Playwright report wrapper lists {wrapped if wrapped is not None else 'no'} "
                f"tests in {repository} {lane}, where its own config lists {own}, so the train's "
                "shards would not run its suite")
    return findings


def _listed(run, directory: Path, arguments: list[str]) -> int | None:
    completed = run(["npx", "playwright", "test", "--list", *arguments], cwd=str(directory),
                    capture_output=True, text=True, check=False, timeout=300)
    match = TOTAL.search(completed.stdout or "")
    return int(match.group(1)) if completed.returncode == 0 and match else None

"""Retain bounded, allowlisted test evidence before isolated build cleanup."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import uuid

REPORT_DIRECTORIES = {'test-results', 'playwright-report', 'surefire-reports',
                      'failsafe-reports', 'coverage'}
SKIP_DIRECTORIES = {'node_modules', '.git', '.angular', 'npm-cache'}
COMMAND_LOG = '.cedar-build-commands.log'
MAX_BYTES = 250 * 1024 * 1024


def retain_failure(workspace, cedar_home, *, limit=MAX_BYTES):
    workspace = Path(workspace)
    destination = Path(cedar_home) / '.cedar' / 'build-reports' / 'failures' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, mode=0o700)
    copied, skipped, total = [], [], 0
    for directory, children, files in os.walk(workspace, followlinks=False):
        root = Path(directory)
        children[:] = sorted(name for name in children
                             if name not in SKIP_DIRECTORIES and not (root / name).is_symlink())
        relative = root.relative_to(workspace)
        for name in sorted(files):
            source = root / name
            if source.is_symlink():
                continue
            if not (REPORT_DIRECTORIES.intersection(relative.parts)
                    or (relative == Path('.') and name == COMMAND_LOG)):
                continue
            target = source.relative_to(workspace)
            size = source.stat().st_size
            if total + size > limit:
                skipped.append(str(target))
                continue
            output = destination / target
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source, output)
            output.chmod(0o600)
            copied.append(str(target))
            total += size
    (destination / 'manifest.json').write_text(json.dumps({
        'workspace': str(workspace), 'bytes': total, 'limitBytes': limit,
        'files': copied, 'omittedForSize': skipped,
    }, indent=2) + '\n')
    return destination


@contextmanager
def failure_diagnostics(workspace, cedar_home, report):
    outcome = {'exitCode': None}
    try:
        yield outcome
    finally:
        # None also covers exceptions/interruptions before a command returned.
        if outcome['exitCode'] != 0:
            try:
                path = retain_failure(workspace, cedar_home)
                report(f'Failed build diagnostics: {path}', markup=False)
            except (OSError, TypeError, ValueError) as error:
                report(f'Could not retain failed build diagnostics: {error}', markup=False)

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
        'kind': 'cedar-failed-build-diagnostics', 'createdAt': datetime.now(timezone.utc).isoformat(),
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


def prune_failures(cedar_home, *, days=14, max_bytes=1024 * 1024 * 1024, apply=False, now=None):
    """Only completed, recognized diagnostic bundles; dry-run unless explicitly applied."""
    import re
    now = now or datetime.now(timezone.utc)
    if days < 0 or max_bytes < 0:
        raise ValueError('Retention age and byte budget must be nonnegative')
    root = Path(cedar_home) / '.cedar/build-reports/failures'
    if root.is_symlink():
        raise ValueError('Refusing a symlinked diagnostic root')
    if not root.exists():
        return []
    bundles = []
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir() or not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{8}', directory.name):
            continue
        manifest = directory / 'manifest.json'
        if manifest.is_symlink() or not manifest.is_file():
            continue
        try:
            record = json.loads(manifest.read_text())
            if record.get('kind') != 'cedar-failed-build-diagnostics':
                continue
            created = datetime.fromisoformat(record['createdAt'])
            if created.tzinfo is None or created > now:
                continue
            size = sum((Path(path) / name).stat().st_size
                       for path, children, files in os.walk(directory, followlinks=False)
                       for name in files if not (Path(path) / name).is_symlink())
            bundles.append((created, directory, size, manifest.read_bytes()))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    retained = 0
    result = []
    for created, directory, size, expected_manifest in sorted(bundles, reverse=True):
        expired = (now - created).total_seconds() >= days * 86400
        over_budget = retained + size > max_bytes
        selected = expired or over_budget
        if not selected:
            retained += size
        item = {'path': str(directory), 'bytes': size, 'action': 'keep',
                'reason': 'age' if expired else 'budget' if over_budget else 'within retention'}
        if selected:
            item['action'] = 'would remove'
            if apply:
                # Recheck after inventory; never follow a replaced root or bundle.
                if root.is_symlink() or directory.is_symlink() or (directory / 'manifest.json').is_symlink():
                    raise ValueError('Diagnostic bundle changed to a symlink during cleanup')
                if (directory / 'manifest.json').read_bytes() != expected_manifest:
                    raise ValueError('Diagnostic bundle changed during cleanup')
                shutil.rmtree(directory)
                item['action'] = 'removed'
        result.append(item)
    return result

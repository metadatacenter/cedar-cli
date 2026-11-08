"""Idle-only workstation sweep. No builds of producers, deployments, publications or role changes."""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from org.metadatacenter.util.InvocationContext import invocation_environment
from org.metadatacenter.util.NodeBuildCheck import require_build_node
from org.metadatacenter.util.Util import Util
from org.metadatacenter.util.local_stack_lock import LocalStackBusy, local_stack

LABEL = 'org.metadatacenter.frontend-performance'
ZONE = ZoneInfo('Europe/Madrid')
IDLE_SECONDS = 600
OTHER_CPU_LIMIT = 60  # macOS ps: 100 is one fully occupied logical CPU.
REPOSITORIES = ('cedar-workspace', 'cedar-template-designer', 'cedar-openview', 'cedar-monitoring',
                'cedar-bridging', 'cedar-content-distribution', 'cedar-embeddable-editor',
                'cedar-embeddable-designer', 'cedar-embeddable-term-picker',
                'cedar-model-typescript-library', 'cedar-design-tokens', 'cedar-development', 'cedar-cli')


def due(now):
    local = now.astimezone(ZONE)
    return local.hour == 3 and 30 <= local.minute < 40


def descendants(rows, owner):
    found = {owner}
    while True:
        expanded = found | {pid for pid, parent, _cpu in rows if parent in found}
        if expanded == found:
            return found
        found = expanded


def idle_sample(exclude=None):
    """Fail closed if activity cannot be read. Never read process arguments or credentials."""
    idle = subprocess.run(['/usr/sbin/ioreg', '-c', 'IOHIDSystem'], check=True, capture_output=True, text=True).stdout
    values = [int(value) / 1e9 for value in re.findall(r'"HIDIdleTime"\s*=\s*(\d+)', idle)]
    if not values:
        raise ValueError('Cannot determine keyboard/mouse idle time')
    table = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,%cpu='], check=True, capture_output=True, text=True).stdout
    rows = []
    for line in table.splitlines():
        pid, parent, cpu = line.split()
        rows.append((int(pid), int(parent), float(cpu)))
    excluded = descendants(rows, os.getpid())
    if exclude:
        excluded |= descendants(rows, exclude)
    return {'idleSeconds': min(values), 'otherCpuPercent': sum(cpu for pid, _p, cpu in rows if pid not in excluded)}


def busy_reason(sample):
    if sample['idleSeconds'] < IDLE_SECONDS:
        return 'Keyboard/mouse activity within the last ten minutes'
    if sample['otherCpuPercent'] > OTHER_CPU_LIMIT:
        return 'Other processes are using more than 60% of one CPU'
    return None


def monitor_environment(home, environment):
    path = home / '.cedar/secrets/frontend-perf-monitor.json'
    if path.stat().st_mode & 0o077:
        raise ValueError('Performance account file must be private (mode 0600)')
    account = json.loads(path.read_text())
    if not account.get('username') or not account.get('password'):
        raise ValueError('Dedicated performance account is incomplete')
    return {**environment, 'CEDAR_PERF_MONITOR_LOGIN': account['username'],
            'CEDAR_PERF_MONITOR_PASSWORD': account['password']}


def prune_reports(root, now):
    """Only this scheduler's marked, strictly named directories; never follow symlinks."""
    for path in root.glob('nightly-*'):
        if path.is_symlink() or not re.fullmatch(r'nightly-\d{8}T\d{6}Z', path.name):
            continue
        marker = path / 'scheduled.json'
        if not marker.is_file() or marker.is_symlink():
            continue
        try:
            data = json.loads(marker.read_text())
        except (ValueError, OSError):
            continue
        if data.get('owner') != LABEL or data.get('cleanupErrors') or data.get('status') not in ('complete', 'failed', 'interrupted'):
            continue
        try:
            age = now - dt.datetime.fromisoformat(data['startedAt'])
        except (ValueError, KeyError, TypeError):
            continue
        if age > dt.timedelta(days=30):
            shutil.rmtree(path)
        elif age > dt.timedelta(days=7) and (path / 'lab').is_dir() and not (path / 'lab').is_symlink():
            shutil.rmtree(path / 'lab')


def run_scheduled(*, check=False):
    home = Path(Util.cedar_home).resolve()
    environment = dict(invocation_environment())
    now = dt.datetime.now(dt.timezone.utc)
    root = home / '.cedar/reports/frontend-performance/scheduled'
    state = {'owner': LABEL, 'startedAt': now.isoformat(), 'status': 'skipped'}
    try:
        state['activity'] = idle_sample()
        reason = busy_reason(state['activity'])
        if not due(now):
            reason = 'Outside the 03:30 Europe/Madrid nightly window'
        state['reason'] = reason
        if check:
            print(json.dumps(state, indent=2))
            return 0
        root.mkdir(parents=True, exist_ok=True)
        previous = json.loads((root / 'last-run.json').read_text()) if (root / 'last-run.json').exists() else {}
        if previous.get('status') == 'running' or previous.get('cleanupErrors'):
            reason = 'Previous run has no verified completion; inspect its report before resuming'
        if previous.get('scheduledDate') == now.astimezone(ZONE).date().isoformat():
            reason = 'Already considered this nightly window'
        if reason:
            state['reason'] = reason
            (root / 'latest.json').write_text(json.dumps(state, indent=2)+'\n')
            print('Performance sweep skipped: '+reason)
            return 0
        state['scheduledDate'] = now.astimezone(ZONE).date().isoformat()
        with local_stack(home, wait=False):
            # Changing sources are not a stable unattended measurement input. Nothing is stashed.
            for repo in REPOSITORIES:
                result = subprocess.run(['git', '-C', str(home / repo), 'status', '--porcelain'],
                                        check=True, capture_output=True, text=True)
                if result.stdout.strip():
                    raise LocalStackBusy(f'{repo} has working-tree changes')
            require_build_node(home / 'cedar-workspace', environment)
            child_environment = monitor_environment(home, environment)
            if shutil.disk_usage(root).free < 10 * 1024**3:
                raise LocalStackBusy('Less than 10 GiB free for production snapshots')
            directory = root / now.strftime('nightly-%Y%m%dT%H%M%SZ')
            directory.mkdir()
            state.update(status='running', report=str(directory))
            # run.mjs requires an empty output directory, so its marker is written after completion.
            (root / 'latest.json').write_text(json.dumps(state, indent=2)+'\n')
            (root / 'last-run.json').write_text(json.dumps(state, indent=2)+'\n')
            log = root / 'latest.log'
            with log.open('w') as stream:
                fresh = subprocess.run(['bash', str(home / 'cedar-cli/cli.sh'), 'check', 'components'],
                                       stdout=stream, stderr=subprocess.STDOUT, env=environment, timeout=120)
                if fresh.returncode:
                    state.update(status='failed', reason='Component freshness check failed; no measurements started')
                elif busy_reason(idle_sample()):
                    state.update(status='interrupted', reason='Workstation became busy during preflight')
                else:
                    script = home / 'cedar-development/ops/e2e/frontend-perf/run.mjs'
                    child = subprocess.Popen(['node', str(script), '--samples=5', '--output='+str(directory)],
                                             cwd=script.parent.parent, stdout=stream, stderr=subprocess.STDOUT,
                                             env=child_environment, start_new_session=True)
                    interrupted = supervise(child, state)
                    measured = json.loads((directory / 'report.json').read_text())
                    clean = not measured.get('cleanupErrors') and measured.get('finishedAt')
                    state['status'] = 'complete' if child.returncode == 0 and measured.get('complete') and clean else 'interrupted' if interrupted and clean else 'failed'
                    state['cleanupErrors'] = measured.get('cleanupErrors', [])
                    if state['cleanupErrors']:
                        state['reason'] = 'Fixture cleanup needs investigation; see report.json'
            state['finishedAt'] = dt.datetime.now(dt.timezone.utc).isoformat()
            (directory / 'scheduled.json').write_text(json.dumps(state, indent=2)+'\n')
            (root / 'latest.json').write_text(json.dumps(state, indent=2)+'\n')
            (root / 'last-run.json').write_text(json.dumps(state, indent=2)+'\n')
            prune_reports(root, now)
            print('Performance sweep: '+state['status']+'; '+str(directory))
            return 0 if state['status'] in ('complete', 'interrupted') else 1
    except LocalStackBusy as error:
        state.update(status='skipped', reason=str(error))
    except Exception as error:
        state.update(status='failed', reason=f'{type(error).__name__}: {error}')
    if not check:
        root.mkdir(parents=True, exist_ok=True)
        # last-run.json remains running on an unexpected failure; later runs fail closed.
        (root / 'latest.json').write_text(json.dumps(state, indent=2)+'\n')
    print(json.dumps(state, indent=2))
    return 1 if state['status'] == 'failed' else 0


def supervise(child, state, activity=idle_sample, sleep=time.sleep, clock=time.monotonic):
    interrupted = False
    stopping = False
    started = clock()
    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
    previous = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        while child.poll() is None:
            sleep(15)
            try:
                reason = ('Schedule controller interrupted' if stopping else
                          '45-minute measurement deadline reached' if clock()-started >= 45*60 else
                          busy_reason(activity(exclude=child.pid)))
            except Exception:
                reason = 'Activity monitoring became unavailable'
            if reason and not interrupted and child.poll() is None:
                # Only the Node suite receives SIGINT: it aborts preparation or closes Chromium,
                # then verifies fixture cleanup. Never kill cleanup requests or the local stack.
                child.send_signal(signal.SIGINT)
                state['reason'] = reason
                interrupted = True
        return interrupted
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def launch_agent(home, environment):
    node = shutil.which('node', path=environment.get('PATH'))
    if not node:
        raise ValueError('Pinned Node is not available')
    return {'Label': LABEL, 'ProgramArguments': ['/bin/bash', str(home / 'cedar-cli/cli.sh'), 'test', 'frontend-performance-schedule'],
            'EnvironmentVariables': {'CEDAR_HOME': str(home), 'PATH': str(Path(node).parent)+':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
            # Launchd uses the host clock. Checking Madrid in the command keeps DST and timezone
            # changes correct; at other hours the command performs no suite work.
            'StartCalendarInterval': {'Minute': 30}, 'ProcessType': 'Background', 'ExitTimeOut': 180,
            'WorkingDirectory': str(home), 'StandardOutPath': str(home / '.cedar/reports/frontend-performance/scheduled/launchd.log'),
            'StandardErrorPath': str(home / '.cedar/reports/frontend-performance/scheduled/launchd.log')}


def configure_schedule(action):
    if sys.platform != "darwin":
        raise ValueError("Workstation scheduling requires macOS launchd")
    home = Path(Util.cedar_home).resolve()
    target = Path.home() / 'Library/LaunchAgents' / (LABEL+'.plist')
    service = f'gui/{os.getuid()}/{LABEL}'
    if action == 'status':
        print(f'Configuration: {target} ({"installed" if target.exists() else "absent"})')
        latest = home / '.cedar/reports/frontend-performance/scheduled/latest.json'
        if latest.exists():
            print(latest.read_text())
        return subprocess.run(['launchctl', 'print', service]).returncode
    if action == 'remove':
        if target.exists():
            subprocess.run(['launchctl', 'bootout', service], check=True)
            target.unlink()
        return 0
    environment = dict(invocation_environment())
    require_build_node(home / 'cedar-workspace', environment)
    if target.exists() or target.is_symlink():
        raise ValueError('Schedule already installed; inspect --status or remove it explicitly before replacing it')
    (home / '.cedar/reports/frontend-performance/scheduled').mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(plistlib.dumps(launch_agent(home, environment)))
    result = subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(target)])
    if result.returncode:
        target.unlink()
    return result.returncode

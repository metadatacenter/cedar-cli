"""One worker budget for every frontend build on this machine.

`--workers` caps what one repository's checks use, and `--jobs` lets several repositories run at
once, so three repositories at twelve workers each can ask a 16-core host for 36. Two builds started
by different sessions ask for twice that. Their browser suites then slow one another and time out
under the load. The pool holds one slot for each core the host has. A command takes its workers from
the pool before it starts and returns them when it ends, so every suite on the machine shares the
cores rather than oversubscribing them.

The pool is a directory at `$CEDAR_HOME/.cedar/worker-pool`, holding one subdirectory per slot in
use, and every build on the machine opens the same one. Creating a directory is atomic, so the
reactor and the gate scripts each frontend runs take slots with nothing more than `mkdir`.

Requests are served in the order they were made. Each waits in `queue`, under a name that sorts by
when it arrived, and only the oldest may take slots. A request for several workers would otherwise
lose every free slot to smaller ones and wait as long as they kept arriving.

A holder can die without returning its slots, and the pool outlives every build, so each slot and
each request records its process and the boot that process started in. Either is stale once that
process has exited, once the machine has restarted since, or after twelve hours, longer than any
command holds a slot. A boot is part of the record because process numbers repeat after a restart.
"""
from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from org.metadatacenter.util.InvocationContext import cancellation_requested

POOL_VARIABLE = 'CEDAR_WORKER_POOL'

# Longer than any command holds a slot, so an older slot or request has lost its holder.
STALE_SECONDS = 12 * 3600
# Two readings of one boot's start time can differ by a little clock adjustment.
_BOOT_TOLERANCE = 60
# A holder writes its slot's owner within moments of taking it.
_UNWRITTEN_SECONDS = 60

_NPM_RUN = re.compile(r'npm run ([\w:.-]+)')
_NPM_SCRIPT = re.compile(r'npm (?:--prefix \S+ )?(?:run(?:-script)? \S|test\b)')


class WorkerPool:
    def __init__(self, directory, size):
        self.directory = Path(directory)
        self.size = size

    @classmethod
    def shared(cls, cedar_home, size=None):
        """The machine's pool under `cedar_home`, created on first use.

        Its size is the host's core count. A pool found idle with another size, as when an estate
        moves to a new machine, takes this host's.
        """
        size = size or os.cpu_count() or 1
        directory = Path(cedar_home) / '.cedar' / 'worker-pool'
        (directory / 'queue').mkdir(parents=True, exist_ok=True)
        pool = cls(directory, size)
        recorded = pool._recorded_size()
        if recorded is not None and recorded != size and pool._busy():
            pool.size = recorded
        elif recorded != size:
            staged = directory / f'size.{os.getpid()}.{uuid.uuid4().hex[:8]}'
            staged.write_text(f'{size}\n')
            staged.replace(directory / 'size')
        return pool

    def take(self, want, minimum):
        """Take up to `want` free slots, or none while fewer than `minimum` of them are free."""
        taken = []
        for index in range(self.size):
            if len(taken) == want:
                break
            slot = self.directory / f'slot-{index}'
            if _claim(slot):
                taken.append(slot)
        if len(taken) < minimum:
            self.give(taken)
            return []
        return taken

    @staticmethod
    def give(slots):
        for slot in slots:
            shutil.rmtree(slot, ignore_errors=True)

    @contextmanager
    def lease(self, want, poll=0.25):
        """Hold between half of `want` and all of it for the body, once every earlier request is served.

        Yields the number held. A cancelled build stops waiting.
        """
        want = max(1, min(want, self.size))
        name = f'{time.time_ns():020d}-{os.getpid()}-{boot_time()}-{uuid.uuid4().hex[:8]}'
        ticket = self.directory / 'queue' / name
        ticket.touch()
        try:
            while not (self.first() == name and (slots := self.take(want, (want + 1) // 2))):
                if cancellation_requested():
                    raise InterruptedError('Build cancelled')
                time.sleep(poll)
        finally:
            ticket.unlink(missing_ok=True)
        try:
            yield len(slots)
        finally:
            self.give(slots)

    def first(self):
        """The oldest request still waiting, dropping those whose holder is gone."""
        queue = self.directory / 'queue'
        for name in sorted(path.name for path in queue.iterdir()):
            parts = name.split('-')
            try:
                arrived, pid, boot = int(parts[0]) / 1e9, int(parts[1]), int(parts[2])
            except (IndexError, ValueError):
                arrived, pid, boot = 0, 0, 0
            if _holds(pid, boot, arrived):
                return name
            (queue / name).unlink(missing_ok=True)
        return None

    def _recorded_size(self):
        try:
            return int((self.directory / 'size').read_text())
        except (OSError, ValueError):
            return None

    def _busy(self):
        return any(self.directory.glob('slot-*')) or any((self.directory / 'queue').iterdir())


def runs_gate(command, cwd):
    """Whether `command` runs the repository's gate script.

    A gate schedules its own stages and takes each stage's workers from the pool, so the command
    that starts it takes none.
    """
    match = _NPM_RUN.match(command.strip())
    if not match:
        return False
    try:
        scripts = json.loads((Path(cwd) / 'package.json').read_text()).get('scripts', {})
    except (OSError, ValueError):
        return False
    return 'scripts/test-gate.mjs' in str(scripts.get(match.group(1), ''))


def wanted(command, budget):
    """How many workers a command asks the pool for.

    Only an npm script starts the build and test tools that read the worker budget. An install or a
    browser download takes one worker, so it never waits for a share of the pool it would not use.
    """
    return budget if _NPM_SCRIPT.match(command.strip()) else 1


@lru_cache(maxsize=1)
def boot_time():
    """When this machine last started, in whole seconds since the epoch."""
    try:
        reading = subprocess.run(['sysctl', '-n', 'kern.boottime'], capture_output=True, text=True,
                                 check=True).stdout
        return int(re.search(r'sec = (\d+)', reading).group(1))
    except (OSError, subprocess.CalledProcessError, AttributeError):
        pass
    try:
        return round(time.time() - float(Path('/proc/uptime').read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _claim(slot):
    for _ in range(2):
        try:
            slot.mkdir()
        except FileExistsError:
            if not _abandoned(slot):
                return False
            shutil.rmtree(slot, ignore_errors=True)
            continue
        (slot / 'owner').write_text(f'{os.getpid()} {boot_time()}\n')
        return True
    return False


def _abandoned(slot):
    """Whether the holder of `slot` is gone.

    A slot whose owner is still being written is in use for a minute, long enough for any holder
    to write it. One still without an owner after that lost its holder in between.
    """
    try:
        taken = slot.stat().st_mtime
    except OSError:
        return False
    try:
        fields = (slot / 'owner').read_text().split()
        pid, boot = int(fields[0]), int(fields[1]) if len(fields) > 1 else boot_time()
    except (OSError, ValueError, IndexError):
        return time.time() - taken > _UNWRITTEN_SECONDS
    return not _holds(pid, boot, taken)


def _holds(pid, boot, since):
    """Whether process `pid`, started in boot `boot`, can still hold what it took at `since`."""
    if abs(boot - boot_time()) > _BOOT_TOLERANCE or time.time() - since > STALE_SECONDS:
        return False
    return _alive(pid)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

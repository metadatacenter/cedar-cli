"""One worker budget for every repository a frontend build runs at once.

`--workers` caps what one repository's checks use, and `--jobs` lets several repositories run at
once, so three repositories at twelve workers each can ask a 16-core host for 36. Their browser
suites then slow one another and time out under the load. The pool holds one slot for each worker
the host runs. A command takes its workers from the pool before it starts and returns them when it
ends, so suites in different repositories share the host rather than oversubscribe it.

The pool is a directory holding one subdirectory per slot in use. Creating a directory is atomic,
so the reactor and the gate scripts each frontend runs take slots with nothing more than `mkdir`.
Each slot records the process holding it, so the slots of a holder that died can be taken back.

Requests are served in the order they were made. Each waits in `queue`, under a name that sorts by
when it arrived, and only the oldest may take slots. A request for several workers would otherwise
lose every free slot to smaller ones and wait as long as they kept arriving.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from org.metadatacenter.util.InvocationContext import cancellation_requested

POOL_VARIABLE = 'CEDAR_WORKER_POOL'

_NPM_RUN = re.compile(r'npm run ([\w:.-]+)')
_NPM_SCRIPT = re.compile(r'npm (?:--prefix \S+ )?(?:run(?:-script)? \S|test\b)')


class WorkerPool:
    def __init__(self, directory, size):
        self.directory = Path(directory)
        self.size = size

    @classmethod
    @contextmanager
    def created(cls, root, size):
        """A pool of `size` slots in a new directory under `root`, removed when the build ends."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='workers-', dir=root))
        (directory / 'queue').mkdir()
        (directory / 'size').write_text(f'{size}\n')
        try:
            yield cls(directory, size)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

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
        ticket = self.directory / 'queue' / f'{time.time_ns():020d}-{os.getpid()}-{uuid.uuid4().hex[:8]}'
        ticket.touch()
        try:
            while not (self.first() == ticket.name and (slots := self.take(want, (want + 1) // 2))):
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
        """The oldest request still waiting, dropping those whose process has exited."""
        queue = self.directory / 'queue'
        for name in sorted(path.name for path in queue.iterdir()):
            if _alive(int(name.split('-')[1])):
                return name
            (queue / name).unlink(missing_ok=True)
        return None


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


def _claim(slot):
    for _ in range(2):
        try:
            slot.mkdir()
        except FileExistsError:
            if not _abandoned(slot):
                return False
            shutil.rmtree(slot, ignore_errors=True)
            continue
        (slot / 'owner').write_text(f'{os.getpid()}\n')
        return True
    return False


def _abandoned(slot):
    """Whether the process that took `slot` has exited. A slot still being written is in use."""
    try:
        owner = int((slot / 'owner').read_text())
    except (OSError, ValueError):
        return False
    return not _alive(owner)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

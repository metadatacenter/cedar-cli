"""One deployment or whole-stack smoke at a time on the local stack.

Builds verify in isolated copies, so two can check sources at once. The local frontends and the
stack a smoke drives are shared. On 2026-10-27 a second reactor redeployed the frontends while a
first was verifying, and two smokes at once would each clean up the other's folders. A reactor holds
this lock from its deployment to the end of its smoke, `cedarcli test e2e` holds it for its smoke,
and a second holder waits. The lock is re-entrant within a process, because the reactor's smoke is
the same smoke `test e2e` runs.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path

LOCK = Path('.cedar') / 'locks' / 'local-stack.lock'

_held = 0


@contextmanager
def local_stack(cedar_home, report=print):
    """Hold the local stack, waiting for another process that holds it."""
    global _held
    if _held:
        _held += 1
        try:
            yield
        finally:
            _held -= 1
        return
    path = Path(cedar_home) / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.pread(descriptor, 64, 0).decode(errors='replace').strip() or 'another process'
            report(f'Waiting for {holder} to finish its local deployment or smoke.')
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.ftruncate(descriptor, 0)
        os.pwrite(descriptor, f'PID {os.getpid()}'.encode(), 0)
        _held = 1
        try:
            yield
        finally:
            _held = 0
    finally:
        # Closing the descriptor releases the lock, also when the process dies holding it.
        os.close(descriptor)

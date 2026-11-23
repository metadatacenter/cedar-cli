"""Stream an owned subprocess; keep shell interpretation explicit at the call site."""
from org.metadatacenter.util.InvocationContext import (
    process_environment, process_cancellation, cancellation_requested,
)
import os
import signal
import select
import threading
import subprocess
import sys


def _spawn(argv, *, cwd, env):
    options = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   cwd=cwd, env=env, shell=False)
    if sys.version_info >= (3, 11):
        return subprocess.Popen(argv, process_group=0, **options)

    # Python 3.10 lacks Popen(process_group). Establish the group in a fresh
    # interpreter, then exec the requested command, preserving its PID and status.
    # Unlike preexec_fn, this is safe when the caller has other threads. Wait for
    # the group to exist before handing it the controlling terminal.
    reader, writer = os.pipe()
    bootstrap = '''import os, signal, sys
os.setpgid(0, 0)
fd = int(sys.argv[1])
os.write(fd, b"1")
os.close(fd)
# Match Popen's restore_signals default after this interpreter's startup.
for name in ('SIGPIPE', 'SIGXFZ', 'SIGXFSZ'):
    if hasattr(signal, name):
        signal.signal(getattr(signal, name), signal.SIG_DFL)
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
'''
    try:
        process = subprocess.Popen(
            [sys.executable, '-c', bootstrap, str(writer), *argv],
            pass_fds=(writer,), **options)
    except BaseException:
        os.close(reader)
        raise
    finally:
        os.close(writer)
    try:
        if os.read(reader, 1) != b'1':
            raise RuntimeError('Could not establish subprocess process group')
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.kill()
        process.wait()
        process.stdout.close()
        raise
    finally:
        os.close(reader)
    return process


class CommandOutput(list):
    """Streamed lines together with the process exit status, including signals."""

    def __init__(self, lines, returncode):
        super().__init__(lines)
        self.returncode = returncode


def _set_foreground(group):
    # The CLI is briefly a background group while returning the terminal to
    # itself. Suppress SIGTTOU only around that terminal ownership operation.
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        os.tcsetpgrp(0, group)
    finally:
        signal.signal(signal.SIGTTOU, previous)


def _child_stopped(pid):
    """Report a stop of the child since the last look, without ever reaping an exited child.

    waitid with WSTOPPED alone reports stops and consumes each report; leaving out WEXITED
    leaves the exit status for Popen to collect.
    """
    try:
        result = os.waitid(os.P_PID, pid, os.WSTOPPED | os.WNOHANG)
    except ChildProcessError:
        return False
    return result is not None and result.si_code == os.CLD_STOPPED


class _TerminalHandoff:
    """The controlling terminal, lent to the child's process group for the life of the command.

    While the child's group holds the terminal, Ctrl-Z stops that group and not the CLI. Its
    parent shell then never learns of a stop, never returns its prompt, and every Ctrl-C after it
    only queues on the stopped processes. So a stop of the child becomes a stop of the CLI: the
    terminal returns to the CLI's group, the CLI stops itself, and the shell reports the job as
    stopped. When the shell resumes the CLI, the child's group is resumed with it, and gets the
    terminal back only if the CLI was resumed in the foreground (`fg` rather than `bg`).
    """

    def __init__(self, shell_group, child_group):
        self.shell_group = shell_group
        self.child_group = child_group
        self.lent = False

    def lend(self):
        _set_foreground(self.child_group)
        self.lent = True
        # A child that tried to read before the handoff may have received SIGTTIN.
        self._continue_child()

    def follow_stop(self):
        if not _child_stopped(self.child_group):
            return
        _set_foreground(self.shell_group)
        self.lent = False
        os.kill(os.getpid(), signal.SIGTSTP)
        # Execution continues here once the shell resumes the CLI.
        if os.tcgetpgrp(0) == self.shell_group:
            _set_foreground(self.child_group)
            self.lent = True
        self._continue_child()

    def restore(self):
        # Take the terminal back only from the child. After `bg` the shell owns it.
        if self.lent:
            _set_foreground(self.shell_group)
            self.lent = False

    def _continue_child(self):
        try:
            os.killpg(self.child_group, signal.SIGCONT)
        except ProcessLookupError:
            pass


def _output_lines(process, tick=None):
    # Poll even a silent subprocess so Ctrl-C can cancel every scheduler worker,
    # and so a stop of a child holding the terminal is noticed.
    if process_cancellation.get() is None and tick is None:
        yield from iter(process.stdout.readline, b'')
        return
    pending = b''
    while True:
        if cancellation_requested():
            raise InterruptedError('Build cancelled')
        if tick is not None:
            tick()
        ready, _, _ = select.select([process.stdout], [], [], 0.2)
        if not ready:
            continue
        chunk = os.read(process.stdout.fileno(), 65536)
        if not chunk:
            if pending:
                yield pending
            return
        pending += chunk
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            yield line


def run_process(argv, *, cwd=None, env=None, on_line=None):
    """Execute literal arguments and reap the process even when streaming is interrupted.

    A private process group lets an aborted reader kill the shell and its children,
    rather than leaving a build running after its CLI has failed. Normal completion
    preserves the exact process status and does not reinterpret output as markup.
    """
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError('Process arguments must be a nonempty sequence, not a shell string')
    shell_group = None
    if threading.current_thread() is threading.main_thread() and os.isatty(0) and os.tcgetpgrp(0) == os.getpgrp():
        shell_group = os.getpgrp()
    process = _spawn(list(argv), cwd=cwd, env=process_environment(env))
    lines = []
    handoff = None
    tick = None
    try:
        if shell_group is not None:
            handoff = _TerminalHandoff(shell_group, process.pid)
            handoff.lend()
            tick = handoff.follow_stop
        for raw in _output_lines(process, tick):
            line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
            if line.strip():
                lines.append(line)
                if on_line is not None:
                    on_line(line)
        if process_cancellation.get() is None and tick is None:
            return CommandOutput(lines, process.wait())
        while True:
            if cancellation_requested():
                raise InterruptedError('Build cancelled')
            if tick is not None:
                tick()
            try:
                return CommandOutput(lines, process.wait(timeout=0.2))
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        # Streaming cannot continue, so terminate the entire owned group before
        # reaping. Do not let a shell's child keep writing to an abandoned pipe.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # The leader has already been reaped on this path. macOS may
                # refuse signaling the remaining orphan/zombie group. Preserve
                # the original interruption, but don't silently claim cleanup.
                if process.returncode is None:
                    raise
                print(f'Cleanup warning: process group {process.pid} could not be '
                      'signaled after its parent exited; inspect surviving children.', file=sys.stderr)
            process.wait()
        raise
    finally:
        process.stdout.close()
        if handoff is not None:
            handoff.restore()


def run_shell(script, *, shell, cwd=None, env=None, on_line=None):
    """Execute one explicit shell script; the script owns its internal error policy."""
    if not isinstance(script, str):
        raise ValueError('A shell script must be a string')
    return run_process([shell, '-c', script], cwd=cwd, env=env, on_line=on_line)

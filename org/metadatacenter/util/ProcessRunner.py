"""Stream an owned subprocess; keep shell interpretation explicit at the call site."""
from org.metadatacenter.util.InvocationContext import process_environment
import os
import signal
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


def run_process(argv, *, cwd=None, env=None, on_line=None):
    """Execute literal arguments and reap the process even when streaming is interrupted.

    A private process group lets an aborted reader kill the shell and its children,
    rather than leaving a build running after its CLI has failed. Normal completion
    preserves the exact process status and does not reinterpret output as markup.
    """
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError('Process arguments must be a nonempty sequence, not a shell string')
    foreground = None
    if os.isatty(0) and os.tcgetpgrp(0) == os.getpgrp():
        foreground = os.getpgrp()
    process = _spawn(list(argv), cwd=cwd, env=process_environment(env))
    lines = []
    try:
        if foreground is not None:
            _set_foreground(process.pid)
            # A child that tried to read before the handoff may have received SIGTTIN.
            try:
                os.killpg(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        for raw in iter(process.stdout.readline, b''):
            line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
            if line.strip():
                lines.append(line)
                if on_line is not None:
                    on_line(line)
        return CommandOutput(lines, process.wait())
    except BaseException:
        # Streaming cannot continue, so terminate the entire owned group before
        # reaping. Do not let a shell's child keep writing to an abandoned pipe.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        process.stdout.close()
        if foreground is not None:
            _set_foreground(foreground)


def run_shell(script, *, shell, cwd=None, env=None, on_line=None):
    """Execute one explicit shell script; the script owns its internal error policy."""
    if not isinstance(script, str):
        raise ValueError('A shell script must be a string')
    return run_process([shell, '-c', script], cwd=cwd, env=env, on_line=on_line)

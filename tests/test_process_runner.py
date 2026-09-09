import os
import errno
import pty
import select
import signal
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from org.metadatacenter.util.ProcessRunner import run_process, run_shell
from org.metadatacenter.worker.Worker import Worker


class ProcessRunnerTest(unittest.TestCase):
    def test_literal_arguments_and_output_decoding(self):
        literal = '$(echo injected); `uname`'
        result = run_process([sys.executable, '-c',
                              'import os,sys; print(sys.argv[1], flush=True); '
                              'os.write(1,b"  indented \\xff\\n"); sys.exit(7)', literal])
        self.assertEqual([literal, '  indented \ufffd'], list(result))
        self.assertEqual(7, result.returncode)

    def test_each_script_runs_and_first_failure_stops_the_list(self):
        result = Worker.execute_generic_shell_commands(
            ['printf first', 'printf second', 'printf failure >&2; exit 9', 'printf forbidden'],
            'test', show_command=False, show_title=False, echo_streams=False)
        self.assertEqual(['first', 'second', 'failure'], list(result))
        self.assertEqual(9, result.returncode)
        self.assertEqual('first\nsecond', Worker.command_list_as_string(['first', 'second']))

    def test_successful_scripts_cwd_and_environment(self):
        with tempfile.TemporaryDirectory() as root:
            result = run_shell('printf "%s\\n" "$AUDIT_VALUE"; pwd', shell='/bin/bash',
                               cwd=root, env={**os.environ, 'AUDIT_VALUE': 'literal value'})
            self.assertEqual('literal value', result[0])
            self.assertEqual(Path(root).resolve(), Path(result[1]).resolve())
            self.assertEqual(0, result.returncode)
        result = Worker.execute_generic_shell_commands(
            ['printf first', 'printf second'], 'test',
            show_command=False, show_title=False, echo_streams=False)
        self.assertEqual(['first', 'second'], list(result))
        self.assertEqual(0, result.returncode)

    def test_interrupted_stream_kills_children_closes_pipe_and_reaps_parent(self):
        child_code = 'import os,time; print(os.getpid(), flush=True); time.sleep(60)'
        parent_code = ('import subprocess,sys; '
                       'subprocess.run([sys.executable,"-c",sys.argv[1]])')
        for error in (RuntimeError('renderer failed'), KeyboardInterrupt()):
            processes, child_pids = [], []
            real_popen = subprocess.Popen
            def record(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                return process
            def interrupt(line):
                child_pids.append(int(line))
                raise error
            with patch('org.metadatacenter.util.ProcessRunner.subprocess.Popen', side_effect=record):
                with self.assertRaises(type(error)):
                    run_process([sys.executable, '-c', parent_code, child_code], on_line=interrupt)
            self.assertIsNotNone(processes[0].returncode)
            self.assertTrue(processes[0].stdout.closed)
            status = subprocess.run(['ps', '-o', 'stat=', '-p', str(child_pids[0])],
                                    text=True, capture_output=True).stdout.strip()
            # An orphan zombie may await init's reap, but no child may keep running.
            self.assertTrue(not status or status.startswith('Z'), status)

    def test_ambiguous_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            run_process('echo ambiguous')
        with self.assertRaises(ValueError):
            run_shell(['echo', 'ambiguous'], shell='/bin/bash')


class ProcessTerminalTest(unittest.TestCase):
    def test_child_can_read_controlling_terminal_and_cli_gets_it_back(self):
        child = "import sys; print('READY', flush=True); print(input(), flush=True)"
        driver = (
            "import os,sys; from org.metadatacenter.util.ProcessRunner import run_process; "
            "run_process([sys.executable,'-c',sys.argv[1]], on_line=lambda s: print(s,flush=True)); "
            "print('RESTORED='+str(os.tcgetpgrp(0)==os.getpgrp()),flush=True)"
        )
        pid, master = pty.fork()
        if pid == 0:
            os.execv(sys.executable, [sys.executable, '-c', driver, child])
        output = b''
        sent = False
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.2)
                if not readable:
                    continue
                try:
                    chunk = os.read(master, 4096)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output += chunk
                if b'READY' in output and not sent:
                    os.write(master, b'terminal-input\n')
                    sent = True
                if b'RESTORED=True' in output:
                    break
            self.assertIn(b'RESTORED=True', output)
            self.assertIn(b'terminal-input', output)
        finally:
            os.close(master)
            # The child owns a private session; closing its terminal ends a stuck
            # driver too. Reap explicitly so a failed test cannot leave it behind.
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)

import io
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
from org.metadatacenter.worker.Worker import Worker


class RecordingProgress:
    def __init__(self):
        self.printed = []
        self.updates = []

    def print(self, value, **kwargs):
        self.printed.append((value, kwargs))

    def update(self, *args, **kwargs):
        self.updates.append((args, kwargs))


class SubprocessOutputTest(unittest.TestCase):

    @staticmethod
    def process_with_output(output, return_code=0):
        process = Mock()
        process.stdout = io.BytesIO(output)
        process.poll.side_effect = AssertionError("subprocess output must not be busy-polled")
        process.wait.return_value = return_code
        return process

    @patch("org.metadatacenter.util.ProcessRunner._spawn")
    def test_shell_task_streams_output_and_waits_without_polling(self, popen):
        process = self.process_with_output(b"first line\n\nsecond line without newline", return_code=7)
        popen.return_value = process
        progress = RecordingProgress()
        task = SimpleNamespace(node_id=3)
        repo = SimpleNamespace(name="cedar-example", repo_type="JAVA")

        output, return_code = ShellTaskExecutor().execute_shell_command(
            task, repo, "example command", "/tmp", progress)

        self.assertEqual(["first line", "second line without newline"], output)
        self.assertEqual(7, return_code)
        process.poll.assert_not_called()
        process.wait.assert_called_once_with()
        self.assertEqual(2, len(progress.updates))

    @patch("org.metadatacenter.worker.Worker.console")
    @patch("org.metadatacenter.util.ProcessRunner._spawn")
    def test_generic_worker_streams_output_and_waits_without_polling(self, popen, worker_console):
        process = self.process_with_output(b"first line\nsecond line\n")
        popen.return_value = process

        output = Worker.execute_generic_shell_commands(["example command"], "Example")

        self.assertEqual(["first line", "second line"], output)
        self.assertEqual(0, output.returncode)
        process.poll.assert_not_called()
        process.wait.assert_called_once_with()
        worker_console.print.assert_any_call("first line", markup=False)
        worker_console.print.assert_any_call("second line", markup=False)

    @patch("org.metadatacenter.worker.Worker.console")
    @patch("org.metadatacenter.util.ProcessRunner._spawn")
    def test_generic_worker_can_hide_an_inline_implementation(self, popen, worker_console):
        popen.return_value = self.process_with_output(b"OK   cedar-infrastructure\n")

        Worker.execute_generic_shell_commands(
            ["several\nlines\nof shell"],
            "Validating CEDAR compose stacks",
            show_command=False,
        )

        worker_console.print.assert_any_call(
            "[yellow]Validating CEDAR compose stacks[/yellow]")
        rendered = " ".join(str(call) for call in worker_console.print.call_args_list)
        self.assertNotIn("several", rendered)


if __name__ == "__main__":
    unittest.main()

    @patch("org.metadatacenter.util.ProcessRunner._spawn")
    def test_isolated_command_log_captures_streamed_failure(self, popen):
        popen.return_value = self.process_with_output(b"failure detail\n", return_code=7)
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'commands.log'
            _, code = ShellTaskExecutor().execute_shell_command(
                SimpleNamespace(node_id=3), SimpleNamespace(name='example', repo_type='JAVA'),
                'failing command', directory, RecordingProgress(),
                environment={'CEDAR_BUILD_DIAGNOSTIC_LOG': str(log)})
            self.assertEqual(7, code)
            self.assertIn('Command: failing command', log.read_text())
            self.assertIn('failure detail', log.read_text())

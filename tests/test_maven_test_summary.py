import unittest

from org.metadatacenter import build_scheduler
from org.metadatacenter.util.MavenTestSummary import LIMIT, MavenTestSummary

OUTPUT = '''[INFO] Running org.metadatacenter.FolderTest
[INFO] Tests run: 12, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 1.2 s -- in org.metadatacenter.FolderTest
[ERROR] Tests run: 3, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.5 s <<< FAILURE! -- in org.metadatacenter.SharingTest
[ERROR] org.metadatacenter.SharingTest.revokes -- Time elapsed: 0.1 s <<< FAILURE!
org.opentest4j.AssertionFailedError: expected: <403> but was: <200>
\tat org.metadatacenter.SharingTest.revokes(SharingTest.java:42)
[INFO] Results:
[ERROR] Failures: 
[ERROR]   SharingTest.revokes:42 expected: <403> but was: <200>
[INFO] 
[ERROR] Tests run: 15, Failures: 1, Errors: 0, Skipped: 0
[ERROR] Surefire is going to kill self fork JVM. The exit has elapsed 30 seconds after System.exit(0).
[WARNING] Rule violated for bundle cedar-bridge-server-application: lines covered ratio is 0.00, but expected minimum is 0.90
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-surefire-plugin:3.6.1:test (default-test) on project cedar-resource-server-application: There are test failures.
[ERROR] -> [Help 1]
'''


class MavenTestSummaryTest(unittest.TestCase):

    def test_the_summary_keeps_what_says_which_test_failed_and_why_the_build_stopped(self):
        summary = MavenTestSummary()
        for line in OUTPUT.splitlines():
            summary.add(f'\x1b[1m{line}\x1b[0m')

        self.assertEqual([
            '[ERROR] Tests run: 3, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.5 s <<< FAILURE! '
            '-- in org.metadatacenter.SharingTest',
            '[ERROR] org.metadatacenter.SharingTest.revokes -- Time elapsed: 0.1 s <<< FAILURE!',
            '[ERROR] Failures:',
            '[ERROR]   SharingTest.revokes:42 expected: <403> but was: <200>',
            '[ERROR] Tests run: 15, Failures: 1, Errors: 0, Skipped: 0',
            '[ERROR] Surefire is going to kill self fork JVM. The exit has elapsed 30 seconds after System.exit(0).',
            '[WARNING] Rule violated for bundle cedar-bridge-server-application: lines covered ratio is 0.00, '
            'but expected minimum is 0.90',
            '[ERROR] Failed to execute goal org.apache.maven.plugins:maven-surefire-plugin:3.6.1:test '
            '(default-test) on project cedar-resource-server-application: There are test failures.',
        ], summary.lines)

    def test_the_summary_is_bounded(self):
        summary = MavenTestSummary()
        for _ in range(LIMIT + 10):
            summary.add('[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0')
        self.assertEqual(LIMIT, len(summary.lines))

    def test_a_command_record_carries_its_summary_only_when_there_is_one(self):
        records = []
        token = build_scheduler._timings.set(records)
        try:
            build_scheduler.record_timing('cedar-x', './mvnw clean install', 0, 1, (), ['[ERROR] Tests run: 1'])
            build_scheduler.record_timing('cedar-y', 'npm ci', 0, 0)
        finally:
            build_scheduler._timings.reset(token)
        self.assertEqual(['[ERROR] Tests run: 1'], records[0]['testSummary'])
        self.assertNotIn('testSummary', records[1])

    def test_a_maven_command_reports_its_summary_and_another_command_none(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor

        def shell(_command, *, on_line, **_kwargs):
            for line in OUTPUT.splitlines():
                on_line(line)
            return SimpleNamespace(returncode=1)

        task = SimpleNamespace(node_id=1)
        repo = SimpleNamespace(name='cedar-x', repo_type='JAVA')
        with patch('org.metadatacenter.taskexecutor.ShellTaskExecutor.run_shell', side_effect=shell), \
                patch('org.metadatacenter.taskexecutor.ShellTaskExecutor.record_timing') as timing:
            ShellTaskExecutor().execute_shell_command(task, repo, './mvnw clean install', '/tmp', Mock())
            ShellTaskExecutor().execute_shell_command(task, repo, 'npm ci', '/tmp', Mock())

        maven, npm = (call.args[5] for call in timing.call_args_list)
        self.assertIn('[ERROR] Tests run: 15, Failures: 1, Errors: 0, Skipped: 0', maven)
        self.assertEqual((), npm)


if __name__ == '__main__':
    unittest.main()

import contextlib
import datetime as dt
import io
import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from org.metadatacenter import frontend_performance_schedule as schedule


class PerformanceScheduleTest(unittest.TestCase):
    def test_madrid_window_tracks_dst_and_never_runs_after_wake_outside_window(self):
        self.assertTrue(schedule.due(dt.datetime(2026, 11, 9, 2, 30, tzinfo=dt.timezone.utc)))
        self.assertTrue(schedule.due(dt.datetime(2026, 7, 9, 1, 30, tzinfo=dt.timezone.utc)))
        self.assertFalse(schedule.due(dt.datetime(2026, 11, 9, 5, 30, tzinfo=dt.timezone.utc)))

    def test_busy_user_and_background_work_are_independent_reasons_to_skip(self):
        self.assertIsNone(schedule.busy_reason({'idleSeconds':600, 'otherCpuPercent':60}))
        self.assertIsNotNone(schedule.busy_reason({'idleSeconds':599, 'otherCpuPercent':0}))
        self.assertIsNotNone(schedule.busy_reason({'idleSeconds':3600, 'otherCpuPercent':61}))

    def test_cpu_exclusion_follows_browser_descendants_not_all_chrome_processes(self):
        rows=[(1, 0, 1), (10, 1, 1), (11, 10, 50), (12, 11, 100), (20, 1, 200)]
        self.assertEqual(schedule.descendants(rows, 10), {10, 11, 12})

    def test_busy_or_unknown_activity_interrupts_only_the_suite_once_and_waits_for_cleanup(self):
        for activity in (lambda **_: {'idleSeconds':0, 'otherCpuPercent':0}, Mock(side_effect=ValueError('unavailable'))):
            child=Mock(pid=123)
            child.poll.side_effect=[None, None, None, None, 0]
            state={}
            self.assertTrue(schedule.supervise(child, state, activity=activity, sleep=lambda _seconds: None))
            child.send_signal.assert_called_once_with(signal.SIGINT)
            child.kill.assert_not_called()
            self.assertIn('reason', state)

    def test_previous_unverified_run_or_cleanup_failure_prevents_another_run(self):
        for previous in ({'status':'running'}, {'status':'failed', 'cleanupErrors':['fixture still exists']}):
            with tempfile.TemporaryDirectory() as directory:
                home=Path(directory); root=home/'.cedar/reports/frontend-performance/scheduled'; root.mkdir(parents=True)
                (root/'last-run.json').write_text(json.dumps(previous))
                with patch.object(schedule.Util, 'cedar_home', directory), patch.object(schedule, 'invocation_environment', return_value={}), \
                        patch.object(schedule, 'due', return_value=True), \
                        patch.object(schedule, 'idle_sample', return_value={'idleSeconds':1000, 'otherCpuPercent':0}), \
                        patch.object(schedule.subprocess, 'Popen') as launch, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(schedule.run_scheduled(), 0)
                launch.assert_not_called()
                self.assertEqual(json.loads((root/'last-run.json').read_text()), previous)

    def test_account_is_private_and_credentials_are_only_child_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); secret=home/'.cedar/secrets/frontend-perf-monitor.json'; secret.parent.mkdir(parents=True)
            secret.write_text(json.dumps({'username':'fixture', 'password':'not-a-real-secret'})); secret.chmod(0o600)
            result=schedule.monitor_environment(home, {'PATH':'node'})
            self.assertEqual(result['CEDAR_PERF_MONITOR_LOGIN'], 'fixture')
            secret.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'private'):
                schedule.monitor_environment(home, {})

    def test_retention_only_prunes_owned_completed_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); now=dt.datetime(2026, 11, 8, tzinfo=dt.timezone.utc)
            old=root/'nightly-20260901T023000Z'; old.mkdir()
            (old/'scheduled.json').write_text(json.dumps({'owner':schedule.LABEL,'status':'complete','startedAt':'2026-09-01T02:30:00+00:00'}))
            unowned=root/'nightly-20260902T023000Z'; unowned.mkdir()
            recent=root/'nightly-20261030T023000Z'; (recent/'lab').mkdir(parents=True)
            (recent/'report.json').write_text('{}')
            (recent/'scheduled.json').write_text(json.dumps({'owner':schedule.LABEL,'status':'complete','startedAt':'2026-10-30T02:30:00+00:00'}))
            schedule.prune_reports(root, now)
            self.assertFalse(old.exists()); self.assertTrue(unowned.exists())
            self.assertFalse((recent/'lab').exists()); self.assertTrue((recent/'report.json').exists())

    def test_launch_agent_has_no_credentials_and_does_not_wake_the_machine(self):
        with patch.object(schedule.shutil, 'which', return_value='/node24/bin/node'):
            agent=schedule.launch_agent(Path('/cedar'), {'PATH':'unused', 'PASSWORD':'private'})
        self.assertEqual(agent['StartCalendarInterval'], {'Minute':30})
        self.assertEqual(set(agent['EnvironmentVariables']), {'CEDAR_HOME','PATH'})
        self.assertNotIn('RunAtLoad', agent)

    def test_completed_sweep_records_cleanup_and_cannot_run_twice_in_one_window(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            def launch(command, **kwargs):
                output=Path(next(value.split('=',1)[1] for value in command if value.startswith('--output=')))
                self.assertEqual(list(output.iterdir()), [])
                (output/'report.json').write_text(json.dumps({'complete':True,'finishedAt':'done','cleanupErrors':[]}))
                return Mock(pid=123, returncode=0, poll=Mock(return_value=0))
            with patch.object(schedule.Util, 'cedar_home', directory), patch.object(schedule, 'invocation_environment', return_value={}), \
                    patch.object(schedule, 'due', return_value=True), \
                    patch.object(schedule, 'idle_sample', return_value={'idleSeconds':1000,'otherCpuPercent':0}), \
                    patch.object(schedule, 'require_build_node'), patch.object(schedule, 'monitor_environment', return_value={}), \
                    patch.object(schedule.shutil, 'disk_usage', return_value=Mock(free=20*1024**3)), \
                    patch.object(schedule.subprocess, 'run', return_value=Mock(stdout='',returncode=0)), \
                    patch.object(schedule.subprocess, 'Popen', side_effect=launch) as process, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(schedule.run_scheduled(),0)
                self.assertEqual(schedule.run_scheduled(),0)
            process.assert_called_once()
            state=json.loads((home/'.cedar/reports/frontend-performance/scheduled/last-run.json').read_text())
            self.assertEqual(state['status'],'complete')
            self.assertEqual(state['cleanupErrors'],[])

    def test_hung_measurement_hits_deadline_and_still_gets_graceful_cleanup(self):
        child=Mock(pid=123); child.poll.side_effect=[None,None,0]
        state={}; ticks=iter([0,2701])
        self.assertTrue(schedule.supervise(child,state,activity=Mock(),sleep=lambda _s:None,clock=lambda:next(ticks)))
        child.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertIn('deadline',state['reason'])

    def test_non_macos_install_refuses_before_writing_configuration(self):
        with patch.object(schedule.sys, 'platform', 'linux'), patch.object(schedule.Path, 'home') as home:
            with self.assertRaisesRegex(ValueError, 'macOS'):
                schedule.configure_schedule('install')
            home.assert_not_called()

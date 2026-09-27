import copy
import unittest
from unittest.mock import patch
from rich.console import Console
from org.metadatacenter.release_timings import timed_stage, timed_wait, timing_summary, render_timings

class State:
    def __init__(self): self.manifest = {'releaseVersion':'1.0.0','releaseRepositories':['repo']}
    def read_current_manifest(self): return copy.deepcopy(self.manifest), None
    def update_current_manifest(self, changes): self.manifest.update(copy.deepcopy(changes)); return self.read_current_manifest()

class TimingsTest(unittest.TestCase):
    def test_execution_excludes_measured_waits_and_retries_keep_attempts(self):
        state = State()
        with patch('org.metadatacenter.release_timings.time.monotonic', side_effect=[0,2,6,10]):
            with timed_stage(state, 'development'):
                timed_wait('ci', lambda seconds: None, 4)
        record = state.manifest['stageTimings'][0]
        self.assertEqual(10, record['elapsedSeconds'])
        self.assertEqual(4, record['ciWaitSeconds'])
        self.assertEqual(6, record['executionSeconds'])
        with self.assertRaisesRegex(RuntimeError, 'failure'):
            with timed_stage(state, 'development'):
                raise RuntimeError('failure')
        records = state.manifest['stageTimings']
        self.assertEqual(2, records[1]['attempt'])
        self.assertEqual('failed', records[1]['status'])
        self.assertEqual(2, timing_summary(state.manifest)['development']['attempts'])

    def test_incomplete_and_different_workloads_are_not_compared(self):
        state = State()
        with timed_stage(state, 'builds'): pass
        baseline = copy.deepcopy(state.manifest); baseline['releaseVersion'] = '0.9.0'
        console = Console(width=200)
        with console.capture() as capture:
            render_timings(state.manifest, console, baseline)
        self.assertIn('execution delta', capture.get())
        baseline['stageTimings'][0]['workload'] = 'other'
        with console.capture() as capture:
            render_timings(state.manifest, console, baseline)
        self.assertIn('comparison unavailable', capture.get())
        state.manifest['stageTimings'][0]['status'] = 'running'
        with console.capture() as capture:
            render_timings(state.manifest, console, state.manifest)
        self.assertIn('[incomplete]', capture.get())
        self.assertNotIn('execution delta', capture.get())

    def test_legacy_ledger_does_not_invent_zero_timings(self):
        console = Console()
        with console.capture() as capture: render_timings({}, console)
        self.assertIn('unavailable', capture.get())

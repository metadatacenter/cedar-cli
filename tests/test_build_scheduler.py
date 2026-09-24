import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextvars import ContextVar

from org.metadatacenter.build_scheduler import dependencies, run_graph, _cancel
from org.metadatacenter.util.ProcessRunner import run_process


class SchedulerTest(unittest.TestCase):
    def test_overlap_bound_and_producer_order_with_context(self):
        active = 0
        maximum = 0
        finished = set()
        lock = threading.Lock()
        gate = threading.Barrier(2)
        context = ContextVar('test', default=None)
        context.set('selected artifacts')
        def run(i):
            nonlocal active, maximum
            self.assertEqual('selected artifacts', context.get())
            with lock:
                if i == 2:
                    self.assertEqual({0, 1}, finished)
                active += 1
                maximum = max(active, maximum)
            if i < 2:
                gate.wait(timeout=5)
            with lock:
                active -= 1
                finished.add(i)
            return 0
        result = run_graph([0, 1, 2], {0:set(), 1:set(), 2:{0,1}}, run, 2)
        self.assertEqual({0:0, 1:0, 2:0}, result)
        self.assertEqual(2, maximum)

    def test_failure_blocks_transitive_consumers_but_continues_independent_work(self):
        seen = []
        def run(i):
            seen.append(i)
            return 7 if i == 0 else 0
        results = run_graph(list(range(4)), {0:set(), 1:{0}, 2:{1}, 3:set()}, run, 2, fail_fast=False)
        self.assertEqual({0,3}, set(seen))
        self.assertEqual({0:7, 1:125, 2:125, 3:0}, results)

    def test_fail_fast_and_serial_limit(self):
        seen = []
        results = run_graph([0,1], {0:set(),1:set()}, lambda i: seen.append(i) or 9, 1)
        self.assertEqual([0], seen)
        self.assertEqual(125, results[1])

    def test_nested_alias_dependency_and_exclusive_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = []
            for name, isolated, producer in [('parent',False,False),('tokens',True,True),('editor',True,True),('app',True,False),('maven',False,False)]:
                path=root/name
                path.mkdir()
                tasks.append(SimpleNamespace(repo=SimpleNamespace(name=name,published_package_path='.' if producer else None),
                    parameters={'isolated_frontend_build':isolated},command_list=[]))
            (root/'editor/visual').mkdir()
            (root/'editor/visual/package.json').write_text(json.dumps({'dependencies':{'alias':'npm:@scope/tokens@1'}}))
            (root/'app/package.json').write_text(json.dumps({'dependencies':{'@scope/editor':'1'}}))
            with patch('org.metadatacenter.build_scheduler.Util.get_wd', side_effect=lambda r:str(root/r.name)):
                edges=dependencies(tasks)
            self.assertEqual({0}, edges[1])
            self.assertEqual({0,1}, edges[2])
            self.assertEqual({0,2}, edges[3])
            self.assertEqual({0,1,2,3}, edges[4])
            (root/'tokens/package.json').write_text(json.dumps({'dependencies':{'editor':'1'}}))
            with patch('org.metadatacenter.build_scheduler.Util.get_wd', side_effect=lambda r:str(root/r.name)):
                with self.assertRaisesRegex(RuntimeError,'Cyclic'):
                    dependencies(tasks)

    def test_cancel_reaps_silent_worker(self):
        event=threading.Event()
        token=_cancel.set(event)
        timer=threading.Timer(0.2,event.set)
        started=time.monotonic()
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                run_process([sys.executable,'-c','import time; time.sleep(60)'])
            self.assertLess(time.monotonic()-started, 5)
        finally:
            _cancel.reset(token)
            timer.join()

    def test_cancel_after_child_closes_output(self):
        event = threading.Event()
        token = _cancel.set(event)
        timer = threading.Timer(0.2, event.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(InterruptedError):
                run_process([sys.executable, '-c',
                             'import os,time; os.close(1); os.close(2); time.sleep(60)'])
            self.assertLess(time.monotonic() - started, 5)
        finally:
            _cancel.reset(token)
            timer.join()

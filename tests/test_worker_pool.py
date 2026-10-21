import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from org.metadatacenter import build_scheduler
from org.metadatacenter.taskexecutor.ShellTaskExecutor import ShellTaskExecutor
from org.metadatacenter.util.InvocationContext import process_cancellation
from org.metadatacenter.util.worker_pool import POOL_VARIABLE, WorkerPool, runs_gate, wanted


def held(pool):
    return sorted(path.name for path in pool.directory.glob('slot-*'))


def exited_pid():
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait()
    return child.pid


class WorkerPoolTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)

    def pool(self, size):
        context = WorkerPool.created(self.root.name, size)
        pool = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return pool

    def test_a_created_pool_records_its_size_and_is_removed_afterwards(self):
        with WorkerPool.created(self.root.name, 6) as pool:
            directory = pool.directory
            self.assertEqual('6', (directory / 'size').read_text().strip())
        self.assertFalse(directory.exists())

    def test_takes_what_is_free_and_nothing_below_the_minimum(self):
        pool = self.pool(4)
        first = pool.take(3, 2)
        self.assertEqual(3, len(first))
        self.assertEqual([], pool.take(4, 2))
        self.assertEqual(3, len(held(pool)))
        second = pool.take(4, 1)
        self.assertEqual(1, len(second))
        pool.give(first + second)
        self.assertEqual([], held(pool))

    def test_each_slot_names_its_holder(self):
        pool = self.pool(2)
        slots = pool.take(1, 1)
        self.assertEqual(str(os.getpid()), (slots[0] / 'owner').read_text().strip())

    def test_a_slot_whose_holder_exited_is_taken_back(self):
        pool = self.pool(2)
        for index, owner in enumerate((exited_pid(), os.getpid())):
            slot = pool.directory / f'slot-{index}'
            slot.mkdir()
            (slot / 'owner').write_text(f'{owner}\n')
        taken = pool.take(2, 1)
        self.assertEqual(['slot-0'], [slot.name for slot in taken])

    def test_a_slot_still_being_written_stays_with_its_holder(self):
        pool = self.pool(1)
        (pool.directory / 'slot-0').mkdir()
        self.assertEqual([], pool.take(1, 1))

    def test_a_lease_waits_for_half_its_want_and_returns_what_it_held(self):
        pool = self.pool(4)
        busy = pool.take(3, 3)
        granted = []
        def lease():
            with pool.lease(4, poll=0.01) as count:
                granted.append(count)
        thread = threading.Thread(target=lease)
        thread.start()
        time.sleep(0.1)
        self.assertEqual([], granted)
        pool.give(busy[:1])
        thread.join(timeout=5)
        self.assertEqual([2], granted)
        pool.give(busy[1:])
        self.assertEqual([], held(pool))

    def test_requests_are_served_in_the_order_they_were_made(self):
        pool = self.pool(4)
        busy = pool.take(4, 4)
        order = []
        def lease(name, want):
            with pool.lease(want, poll=0.01):
                order.append(name)
        threads = [threading.Thread(target=lease, args=('large', 4)), threading.Thread(target=lease, args=('small', 1))]
        for thread in threads:
            thread.start()
            time.sleep(0.05)
        pool.give(busy[:1])
        time.sleep(0.1)
        self.assertEqual([], order, 'the small request overtook the large one')
        pool.give(busy[1:])
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(['large', 'small'], order)

    def test_a_request_left_by_an_exited_process_is_dropped(self):
        pool = self.pool(1)
        (pool.directory / 'queue' / f'{0:020d}-{exited_pid()}-gone').touch()
        with pool.lease(1, poll=0.01) as count:
            self.assertEqual(1, count)
        self.assertEqual([], list((pool.directory / 'queue').iterdir()))

    def test_a_lease_never_asks_for_more_than_the_pool(self):
        pool = self.pool(2)
        with pool.lease(12) as count:
            self.assertEqual(2, count)

    def test_a_cancelled_build_stops_waiting(self):
        pool = self.pool(1)
        pool.take(1, 1)
        event = threading.Event()
        event.set()
        token = process_cancellation.set(event)
        try:
            with self.assertRaises(InterruptedError):
                with pool.lease(1, poll=0.01):
                    pass
        finally:
            process_cancellation.reset(token)


class GateDetectionTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        Path(self.root.name, 'package.json').write_text(json.dumps({'scripts': {
            'test:ci': 'node scripts/test-gate.mjs',
            'test:ci:prebuilt': 'node scripts/test-gate.mjs prebuilt',
            'test:browser': 'playwright test',
        }}))

    def test_a_script_that_runs_the_gate_is_a_gate(self):
        self.assertTrue(runs_gate('npm run test:ci', self.root.name))
        self.assertTrue(runs_gate('npm run test:ci:prebuilt', self.root.name))

    def test_other_commands_are_not(self):
        for command in ('npm run test:browser', 'npm test', 'npm ci', 'npm run missing'):
            self.assertFalse(runs_gate(command, self.root.name), command)
        self.assertFalse(runs_gate('npm run test:ci', Path(self.root.name, 'absent')))

    def test_only_an_npm_script_asks_for_the_budget(self):
        for command in ('npm run build', 'npm test -- --watch=false', 'npm --prefix visual run bundle'):
            self.assertEqual(12, wanted(command, 12), command)
        for command in ('npm ci', 'npm --prefix browser install', 'npm testing',
                        './browser/node_modules/.bin/playwright install --with-deps chromium'):
            self.assertEqual(1, wanted(command, 12), command)


class CommandLeaseTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        Path(self.root.name, 'package.json').write_text(
            json.dumps({'scripts': {'test:ci': 'node scripts/test-gate.mjs', 'test': 'jest'}}))
        context = WorkerPool.created(self.root.name, 16)
        self.pool = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.environment = {'CEDAR_TEST_WORKERS': '12', POOL_VARIABLE: str(self.pool.directory)}

    def lease(self, command, environment=None):
        with patch('org.metadatacenter.taskexecutor.ShellTaskExecutor.worker_pool', return_value=self.pool):
            with ShellTaskExecutor._worker_lease(command, self.root.name,
                                                 self.environment if environment is None else environment) as count:
                return count, len(held(self.pool))

    def test_a_command_holds_the_repository_budget_while_it_runs(self):
        self.assertEqual((12, 12), self.lease('npm test'))
        self.assertEqual([], held(self.pool))

    def test_an_install_or_a_download_holds_one_worker(self):
        for command in ('npm ci', 'npm --prefix browser install',
                        './browser/node_modules/.bin/playwright install --with-deps chromium'):
            self.assertEqual((1, 1), self.lease(command), command)

    def test_a_gate_takes_its_own_workers(self):
        self.assertEqual((None, 0), self.lease('npm run test:ci'))

    def test_a_build_without_a_pool_holds_nothing(self):
        self.assertEqual((None, 0), self.lease('npm test', {'CEDAR_TEST_WORKERS': '12'}))
        self.assertIsNone(build_scheduler.worker_pool())


if __name__ == '__main__':
    unittest.main()

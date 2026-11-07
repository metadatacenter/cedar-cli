import hashlib
from contextvars import ContextVar
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from org.metadatacenter.release_support.parallel import checked_map
from org.metadatacenter.release_support.publication import ReleaseArtifactPublisher
from org.metadatacenter.release_support.errors import ReleaseError
from org.metadatacenter.release_support.state import ReleaseState
from org.metadatacenter.release_support.lifecycle import integrate_active_release


class ReleaseParallelTest(unittest.TestCase):
    def test_checks_inherit_the_invocations_context_without_sharing_mutations(self):
        selected = ContextVar('selected_release_environment', default='missing')
        token = selected.set('resolved profile')
        try:
            def check(value):
                inherited = selected.get()
                selected.set(value)
                return inherited
            self.assertEqual(['resolved profile'] * 8, checked_map(check, range(8)))
            self.assertEqual('resolved profile', selected.get())
        finally:
            selected.reset(token)

    def test_remote_failure_drains_success_and_resume_does_not_push_it_again(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ReleaseState(root=Path(directory))
            state.start({'releaseVersion': '2.9.3', 'remoteConcurrency': {'jobs': 2}})
            state.update_current_manifest({'phase': 'snapshots-published'})
            barrier = threading.Barrier(2, timeout=5)
            failed_recorded = threading.Event()
            owner = threading.get_ident()
            original_update = state.update_current_manifest
            calls, verified = [], []

            def update(changes):
                self.assertEqual(owner, threading.get_ident())
                result = original_update(changes)
                if changes.get('remoteIntegration', {}).get('failedTask'):
                    failed_recorded.set()
                return result

            class Integrator:
                retry = False
                def tasks(self, manifest):
                    return [{'id': name, 'repository': name} for name in ('a', 'b', 'c')]
                def verify_record(self, manifest, record):
                    verified.append(record['id'])
                def integrate(self, manifest, task):
                    calls.append(task['id'])
                    if not self.retry:
                        barrier.wait()
                        if task['id'] == 'a':
                            raise ReleaseError('ref changed')
                        if not failed_recorded.wait(5):
                            raise AssertionError('coordinator did not record the failure')
                    return task

            integrator = Integrator()
            with patch.object(state, 'update_current_manifest', side_effect=update):
                with self.assertRaisesRegex(ReleaseError, 'ref changed'):
                    integrate_active_release(state, integrator)
                manifest, _ = state.read_current_manifest()
                self.assertEqual({'b'}, set(manifest['remoteIntegration']['completedTasks']))
                self.assertEqual({'a', 'b'}, set(calls))
                integrator.retry = True
                result = integrate_active_release(state, integrator)
            self.assertEqual('remote-integrated', result['phase'])
            self.assertEqual(1, calls.count('b'))
            self.assertEqual(['b'], verified)

    def test_bounded_checks_overlap_and_results_keep_input_order(self):
        barrier = threading.Barrier(4, timeout=5)
        lock = threading.Lock()
        active = peak = 0

        def check(value):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait()
            with lock:
                active -= 1
            return value * 2

        self.assertEqual(list(range(0, 16, 2)), checked_map(check, range(8)))
        self.assertEqual(4, peak)

    def test_a_failure_propagates_after_inflight_checks_finish(self):
        barrier = threading.Barrier(2, timeout=5)
        finished = threading.Event()

        def check(value):
            barrier.wait()
            if value == 0:
                raise ReleaseError('changed artifact')
            finished.set()

        with self.assertRaisesRegex(ReleaseError, 'changed artifact'):
            checked_map(check, [0, 1], workers=2)
        self.assertTrue(finished.is_set())

    def test_parallel_maven_verification_checks_every_byte_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = {str(i): str(i).encode() for i in range(8)}
            for name, content in payloads.items():
                (root / name).write_bytes(content)
            barrier = threading.Barrier(4, timeout=5)
            reads = []

            class Http:
                def read(self, url):
                    reads.append(url)
                    barrier.wait()
                    return payloads[url.rsplit('/', 1)[-1]]

            task = {'id': 'maven', 'kind': 'maven-release-upload',
                    'localRepository': directory, 'repository': 'https://registry.example'}
            record = {**task, 'files': {name: hashlib.sha256(body).hexdigest()
                                       for name, body in payloads.items()}}
            publisher = ReleaseArtifactPublisher(ReleaseState(root=root / 'state'), http=Http(), environment={})
            publisher.verify_record({}, record, [task])
            self.assertEqual(8, len(set(reads)))
            # Avoid a barrier in the failing run: cancelled queued reads cannot arrive at it.
            publisher.http.read = lambda url: b'changed'
            with self.assertRaisesRegex(ReleaseError, 'artifact changed'):
                publisher.verify_record({}, record, [task])


if __name__ == '__main__':
    unittest.main()

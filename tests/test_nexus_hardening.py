import io
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch
from org.metadatacenter.nexus_probe import upload_probe, PROBE_BASE
from org.metadatacenter.release_support.errors import NexusRetryableError, ReleaseError
from org.metadatacenter.release_support import lifecycle
from org.metadatacenter.release_support.transport import _raise_command_failure


class NexusHardeningTest(unittest.TestCase):
    environment = {'BMIR_NEXUS_USERNAME':'user', 'BMIR_NEXUS_PASSWORD':'not-logged'}

    def test_probe_writes_reads_and_removes_one_unique_disposable_object(self):
        calls=[]
        stored={}
        def open_request(request, timeout):
            calls.append((request.get_method(),request.full_url))
            self.assertEqual(30,timeout)
            if request.get_method()=='PUT': stored[request.full_url]=request.data
            body=stored.get(request.full_url,b'') if request.get_method()=='GET' else b''
            response=io.BytesIO(body); response.status=200
            return response
        result=upload_probe(environment=self.environment,opener=open_request)
        self.assertEqual(['PUT','GET','DELETE'],[c[0] for c in calls])
        self.assertEqual(1,len({c[1] for c in calls}))
        self.assertTrue(result['url'].startswith(PROBE_BASE))
        self.assertTrue(result['deleted'])
        self.assertEqual(65536,result['bytes'])
        self.assertNotIn('not-logged',str(result))

    def test_probe_cleans_up_even_when_put_response_is_lost(self):
        calls=[]
        def open_request(request, timeout):
            calls.append(request.get_method())
            if request.get_method()=='PUT': raise TimeoutError('response lost')
            response=io.BytesIO();response.status=204
            return response
        with self.assertRaisesRegex(ValueError,'response lost'):
            upload_probe(environment=self.environment,opener=open_request)
        self.assertEqual(['PUT','DELETE'],calls)

    def test_probe_rejects_different_bytes_and_reports_cleanup_failure(self):
        def open_request(request, timeout):
            if request.get_method()=='DELETE': raise urllib.error.HTTPError(request.full_url,403,'denied',{},None)
            response=io.BytesIO(b'wrong');response.status=200
            return response
        with self.assertRaisesRegex(ValueError,'different probe bytes.*cleanup failed'):
            upload_probe(environment=self.environment,opener=open_request)

    def test_nexus_subprocess_fault_has_a_distinct_retry_budget(self):
        with self.assertRaises(NexusRetryableError):
            _raise_command_failure(['npm','publish'],'publication failed',
                                   'HTTP 502 https://nexus.bmir.stanford.edu/repository/npm-cedar/')
        with self.assertRaises(ReleaseError) as caught:
            _raise_command_failure(['npm','publish'],'publication failed',
                                   'HTTP 500 https://nexus.bmir.stanford.edu/repository/npm-cedar/')
        self.assertNotIsInstance(caught.exception,NexusRetryableError)

    def test_release_stops_after_three_nexus_faults_without_losing_state(self):
        state=Mock()
        with patch.object(lifecycle,'advance_active_release',side_effect=NexusRetryableError('PUT HTTP 502')) as advance:
            with self.assertRaisesRegex(ReleaseError,'circuit open after 3'):
                lifecycle._drive_release(state,sleeper=Mock())
        self.assertEqual(3,advance.call_count)
        update=state.update_current_manifest.call_args.args[0]
        self.assertIsNone(update['retry'])
        self.assertIn('State is retained',update['failure'])

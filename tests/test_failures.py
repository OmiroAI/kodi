import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.failures import DeliveryFailure, classify, execute, latest, provider_failure
from kodi.store import Store


class FailureTests(unittest.TestCase):
    def test_synthetic_table_and_redaction(self):
        cases = [('test_diagnostic','code_test'), ('oom_event','resource'),
                 ('execution_deadline','timeout'), ('http_429','throttle'),
                 ('explicit_quota','quota'), ('http_401','configuration'),
                 ('http_403','configuration'), ('ci_disabled','ci_unavailable'),
                 ('provider_outage','provider_unavailable'), ('killed','unknown')]
        for signal, category in cases:
            with self.subTest(signal=signal):
                self.assertEqual(classify(DeliveryFailure(signal))['category'],category)
        for raw, category in [('HTTP 429 secret-sentinel','throttle'),
                              ('HTTP 429 insufficient_quota secret-sentinel','quota'),
                              ('HTTP 401 secret-sentinel','configuration'),
                              ('process killed secret-sentinel','unknown')]:
            result=classify(provider_failure(raw))
            self.assertEqual(result['category'],category)
            self.assertNotIn('secret-sentinel',json.dumps(result))

    def test_restart_suppression_and_relevant_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.db'; store=Store(path)
            job=store.enqueue('1','example','Example','requirements')['id']
            operation=Mock(side_effect=DeliveryFailure('oom_event'))
            with self.assertRaises(DeliveryFailure):execute(store,job,'qa','candidate',operation)
            store.db.close();store=Store(path)
            try:
                with self.assertRaises(RuntimeError):execute(store,job,'qa','candidate',operation)
                self.assertEqual(operation.call_count,1)
                with self.assertRaises(DeliveryFailure):
                    execute(store,job,'qa','candidate',operation,{'resource_revision':2})
                with self.assertRaises(RuntimeError):
                    execute(store,job,'qa','candidate',operation,{'resource_revision':2})
                self.assertEqual(operation.call_count,2)
                self.assertFalse(store.gate(job,'candidate'))
                self.assertEqual(store.get(job)['attempt'],0)
            finally:store.db.close()

    def test_transient_budget_and_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'state.db');self.addCleanup(store.db.close)
            job=store.enqueue('1','example','Example','requirements')['id']
            operation=Mock(side_effect=DeliveryFailure('http_429',150))
            with patch('kodi.failures.time.time',return_value=100), patch('kodi.failures.time.sleep') as sleep:
                with self.assertRaises(RuntimeError):execute(store,job,'review','candidate',operation)
                self.assertEqual(operation.call_count,3)
                self.assertEqual([c.args[0] for c in sleep.call_args_list],[150,150])
            with self.assertRaises(RuntimeError):execute(store,job,'review','candidate',operation)
            self.assertEqual(operation.call_count,3)
            self.assertEqual(latest(store,job,'review','candidate')['retry_count'],2)

    def test_uncertain_reservation_and_invalid_deadlines(self):
        for deadline in (-1, float('nan'), 901, 'later'):
            with tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory)/'state.db')
                try:
                    operation=Mock(side_effect=DeliveryFailure('http_429',deadline))
                    with patch('kodi.failures.time.sleep') as sleep:
                        with self.assertRaises(DeliveryFailure):execute(store,1,'qa','sha',operation)
                        sleep.assert_not_called()
                    with self.assertRaises(RuntimeError):execute(store,1,'qa','sha',operation)
                    self.assertEqual(operation.call_count,1)
                finally:store.db.close()

    def test_interrupted_dispatch_does_not_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.db';store=Store(path)
            operation=Mock(side_effect=KeyboardInterrupt)
            with self.assertRaises(KeyboardInterrupt):execute(store,1,'review','sha',operation)
            store.db.close();store=Store(path)
            try:
                with self.assertRaises(RuntimeError):execute(store,1,'review','sha',operation)
                self.assertEqual(operation.call_count,1)
                self.assertTrue(latest(store,1,'review','sha')['reservation'])
            finally:store.db.close()

    def test_execution_timeout_is_not_transport_retry(self):
        import subprocess
        value=classify(subprocess.TimeoutExpired(['private-command'],10))
        self.assertEqual(value['category'],'timeout')
        self.assertFalse(value['retry_eligible'])
        self.assertNotIn('private-command',json.dumps(value))

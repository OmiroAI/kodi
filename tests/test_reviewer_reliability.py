import tempfile
import unittest
from pathlib import Path

from kodi.store import Store
from kodi.policy import validate_review
from kodi.reviewer_admission import request, AdmissionBusy


class ReliabilityTests(unittest.TestCase):
    def test_admission_survives_restart_and_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'state.db'
            first, second = Store(path), Store(path)
            job = first.enqueue('example:1', 'project', 'Review', 'Requirements')['id']
            with request(first, job, 'candidate', 'qwen'):
                with self.assertRaises(AdmissionBusy):
                    with request(second, job, 'candidate', 'qwen'):
                        self.fail('concurrent request admitted')
            with request(second, job, 'candidate', 'qwen'):
                pass
            with self.assertRaises(KeyboardInterrupt):
                with request(first, job, 'candidate', 'qwen'):
                    raise KeyboardInterrupt()
            restarted = Store(path)
            with self.assertRaises(AdmissionBusy):
                with request(restarted, job, 'candidate', 'qwen'):
                    self.fail('uncertain request replaced')
            for store in (first, second, restarted):
                store.db.close()

    def test_consistency(self):
        valid = dict(approved=True, criteria_met=True, simple=True,
                     blocking_findings=[], summary='Reviewed source and tests')
        self.assertTrue(validate_review(valid, 'openai', 'qwen')['approved'])
        for change in (dict(approved='true'), dict(criteria_met=False),
                       dict(simple=False), dict(delivery_met=False),
                       dict(summary='Not approved'), dict(summary=''),
                       dict(blocking_findings=['Concrete defect; retracting it'])):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_review({**valid, **change}, 'openai', 'qwen')
        with self.assertRaises(ValueError):
            validate_review(valid, 'openai', 'openai')

class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name)/'state.db')
        self.addCleanup(self.store.db.close)
        self.job = self.store.enqueue('example:2', 'project', 'Review', 'Requirements')['id']

    def test_clarification_is_bounded_and_retains_lineage_across_restart(self):
        import json
        from kodi.review_attempts import execute
        calls = []
        def invalid(ledger, clarification):
            calls.append(clarification)
            raise ValueError('private response must not be retained')
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            execute(self.store, self.job, 'sha', 'qwen', 'model', invalid)
        self.assertEqual(calls, [False, True])
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            execute(self.store, self.job, 'sha', 'qwen', 'model', invalid)
        records = [json.loads(r[0]) for r in self.store.db.execute(
            "SELECT data FROM evidence WHERE kind='review_verdict_attempt'")]
        self.assertEqual(records[2]['parent'], records[0]['token'])
        self.assertNotIn('private response', str(records))

    def test_interrupted_clarification_never_replaced(self):
        from kodi.review_attempts import execute
        def interrupted(*args):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            execute(self.store, self.job, 'sha', 'qwen', 'model', interrupted)
        with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
            execute(self.store, self.job, 'sha', 'qwen', 'model', lambda *args: self.fail())

    def test_invalid_findings_survive_clarification_and_omission(self):
        from kodi.coordinator import Coordinator
        from unittest.mock import patch
        c = Coordinator.__new__(Coordinator)
        c.store, c.config = self.store, {'reviewer': 'gemini'}
        invalid = dict(approved=True, criteria_met=True, simple=True,
                       blocking_findings=['Concrete defect'], summary='Reviewed')
        valid = dict(invalid, blocking_findings=[])
        with patch('kodi.coordinator.run_worker', side_effect=[(invalid, {}, 1), (valid, {}, 1)]):
            verdict = c.review_provider({'id': self.job}, Path(self.directory.name), 'sha', 'Source', 'gemini')
        self.assertFalse(verdict['approved'])
        self.assertTrue(verdict['resolution_blockers'])
        self.assertEqual(len(self.store.findings(self.job)), 1)

    def test_timings_are_distinct_and_counted_once(self):
        from kodi.usage import usage_report
        with request(self.store, self.job, 'sha', 'qwen') as admission:
            admission['request_seconds'] = 3
        timing = usage_report(self.store, 'tasks')[str(self.job)]['review_request_timing']
        self.assertEqual(timing['requests'], 1)
        self.assertEqual(timing['request_seconds'], 3)
        self.assertGreaterEqual(timing['queue_seconds'], 0)
        self.assertEqual(usage_report(self.store, 'tasks')[str(self.job)]['review_request_timing'], timing)

    def test_dispatch_refuses_unsupported_or_same_family_models(self):
        from kodi.coordinator import Coordinator
        from unittest.mock import patch
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        for model in ('unknown', 'gpt-5'):
            c.config = {'qwen_model': model}
            with patch.object(c, '_review_provider') as transport:
                with self.assertRaises(RuntimeError):
                    c.review_provider({'id': self.job}, Path(self.directory.name), 'sha', 'Source', 'qwen')
                transport.assert_not_called()

    def test_unknown_provider_error_is_sanitized(self):
        from kodi.coordinator import Coordinator
        from unittest.mock import patch
        c = Coordinator.__new__(Coordinator)
        c.store, c.config = self.store, {'reviewer': 'gemini'}
        with patch('kodi.coordinator.run_worker', side_effect=RuntimeError('sensitive-response-body')):
            with self.assertRaises(RuntimeError) as error:
                c.review_provider({'id': self.job}, Path(self.directory.name), 'sha', 'Source', 'gemini')
        self.assertNotIn('sensitive-response-body', str(error.exception))
        records = str([r[0] for r in self.store.db.execute('SELECT data FROM evidence')])
        self.assertNotIn('sensitive-response-body', records)

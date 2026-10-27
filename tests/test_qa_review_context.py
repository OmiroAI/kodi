import json
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.coordinator import Coordinator, REVIEW_CONTRACT
from kodi.runner import QuotaExhausted
from kodi.store import Store


class QAContextTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.db.close)
        self.job = self.store.enqueue('fixture', 'system', 'Review', 'Keep all requirements')

    def summary(self, sha='candidate'):
        return self.store.qa_summary(self.job['id'], sha)

    def record(self, **values):
        self.store.evidence(self.job['id'], 'candidate', 'qa',
                            dict(sha='candidate', passed=True, **values))

    def test_pass_and_unknown_metadata(self):
        self.record(suite='system', duration_seconds=1.5, test_count=12,
                    command='python3 -m unittest discover -s tests -v')
        result = self.summary()
        self.assertEqual(result['outcome'], 'passed')
        self.assertEqual(result['test_count'], 12)
        self.assertEqual(result['environment'], 'unknown')
        self.assertEqual(result['suite'], 'system')
        self.assertEqual(result['duration_seconds'], 1.5)
        self.assertEqual(self.summary('changed')['outcome'], 'missing')

    def test_latest_failure_overrides_pass(self):
        self.record()
        self.store.evidence(self.job['id'], 'candidate', 'qa', dict(sha='candidate', passed=False))
        self.assertEqual(self.summary()['outcome'], 'failed')

    def test_missing_stale_malformed_mismatched(self):
        self.assertEqual(self.summary()['outcome'], 'missing')
        self.store.evidence(self.job['id'], 'old', 'qa', dict(sha='old', passed=True))
        self.assertEqual(self.summary()['outcome'], 'missing')
        for data, outcome in [('bad json', 'malformed'), ('[]', 'malformed'),
                              ('{"passed":"true"}', 'malformed'),
                              ('{"sha":"other","passed":true}', 'mismatched')]:
            self.record()
            with self.store.db:
                self.store.db.execute('UPDATE evidence SET data=? WHERE id=(SELECT MAX(id) FROM evidence)', (data,))
            self.assertEqual(self.summary()['outcome'], outcome)

    def test_private_metadata_and_logs_excluded(self):
        self.record(log='PRIVATE_LOG', command='PRIVATE_COMMAND', environment='PRIVATE_HOST',
                    suite='PRIVATE_SUITE', duration_seconds=True, test_count=-1)
        result = json.dumps(self.summary())
        self.assertNotIn('PRIVATE', result)
        self.assertEqual(self.summary()['duration_seconds'], 'unknown')

    def test_primary_fallback_author_limitations_and_gate_separation(self):
        self.record(suite='system')
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = dict(qwen_url='https://example.invalid', qwen_model='qwen', qwen_env='unused')
        author = dict(summary='Author could not run pytest: unavailable', test_steps=['Check behavior'])
        verdict = dict(approved=True, criteria_met=True, simple=True, blocking_findings=[])
        with patch('kodi.coordinator.git', return_value='base'), patch(
                'kodi.coordinator.review_material', return_value=('COMPLETE_DIFF', 'CONTEXT', [])), patch.object(
                c, 'review_provider', side_effect=[QuotaExhausted(), verdict]) as provider:
            self.assertTrue(c.review(self.job, Path('/unused'), 'candidate', author)['approved'])
        prompts = [call.args[3] for call in provider.call_args_list]
        self.assertEqual(prompts[0], prompts[1])
        self.assertIn(json.dumps(self.summary()), prompts[0])
        for text in ('Author could not run pytest', 'Keep all requirements', 'COMPLETE_DIFF',
                     'without waiting for downstream CI', 'challenge test adequacy'):
            self.assertIn(text, prompts[0])
        self.assertFalse(self.store.gate(self.job['id'], 'candidate'))
        # Deterministic character measurement, not a claim about reviewer savings.
        addition = '\n\nIndependent QA (validated stored evidence; private metadata withheld):\n' + json.dumps(self.summary())
        self.assertLess(len(addition), 1024)
        self.assertIn('Passing QA alone is insufficient', REVIEW_CONTRACT)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from kodi.coordinator import Coordinator
from kodi.store import Store
from kodi.delivery import context, requested_type, rejection


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.sqlite'
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.db.close())
        self.requirements = 'Delivery type: IMPLEMENTATION\nImplement retry behavior.\nOld scheduling: planning only; Backlog.'
        self.job = self.store.enqueue('task:1', 'example', 'Retry', self.requirements,
                                      dispatch_stage='Ready')['id']
        self.author = {'summary': 'Implemented bounded retries', 'test_steps': ['Simulate failure'],
                       'delivery_status': 'implemented',
                       'behavioral_evidence': ['Retry limit and failure-path regressions pass']}
        self.review = {'approved': True, 'criteria_met': True, 'simple': True,
                       'blocking_findings': [], 'author_family': 'openai',
                       'reviewer_family': 'google', 'delivery_met': True}

    def evidence(self, author=None, review=None):
        self.store.evidence(self.job, 'candidate', 'author', author or self.author)
        self.store.evidence(self.job, 'candidate', 'qa', {'passed': True})
        self.store.evidence(self.job, 'candidate', 'review', review or self.review)

    def test_ready_implementation_rejects_proposal_despite_baseline_qa(self):
        self.evidence({'summary': 'Proposal only; implementation deferred', 'test_steps': ['Run baseline']})
        self.assertFalse(self.store.gate(self.job, 'candidate'))
        self.assertIn('overrides historical', context(self.store.delivery_contract(self.job)))

    def test_real_behavior_requires_independent_scope_verdict_and_exact_sha(self):
        self.evidence(review={**self.review, 'delivery_met': False})
        self.assertFalse(self.store.gate(self.job, 'candidate'))
        self.evidence()
        self.assertTrue(self.store.gate(self.job, 'candidate'))
        self.assertFalse(self.store.gate(self.job, 'other'))

    def test_restart_and_duplicate_dispatch_preserve_original_contract(self):
        original = self.store.delivery_contract(self.job)
        self.store.db.close()
        self.store = Store(self.path)
        for _ in range(2):
            self.store.enqueue('task:1', 'example', 'Replacement', 'Delivery type: DESIGN', dispatch_stage='Backlog')
        self.assertEqual(self.store.delivery_contract(self.job), original)
        self.assertEqual(self.store.get(self.job)['requirements'], self.requirements)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM evidence WHERE kind='delivery_contract'").fetchone()[0], 1)

    def test_forged_type_noop_and_missing_behavior_cannot_complete(self):
        for change in ({'task_type': 'DESIGN'}, {'summary': 'No durable lesson; no-op'},
                       {'behavioral_evidence': []}, {'delivery_status': 'deferred'}):
            with self.subTest(change=change):
                self.evidence({**self.author, **change})
                self.assertFalse(self.store.gate(self.job, 'candidate'))

    def test_explicit_nonimplementation_scopes_remain_valid(self):
        for scope in ('DESIGN', 'DOCUMENTATION', 'CONFIGURATION', 'BENCHMARK'):
            contract = {'task_type': scope, 'dispatch_stage': 'Ready'}
            self.assertIsNone(rejection(contract, {'summary': 'Scoped deliverable'}))
            self.assertEqual(requested_type('Delivery type: '+scope), scope)

    def test_ambiguous_scope_fails_before_enqueue(self):
        with self.assertRaisesRegex(ValueError, 'Clarify'):
            self.store.enqueue('bad', 'example', 'Bad', 'Delivery type: DESIGN\nDelivery type: IMPLEMENTATION')
        self.assertIsNone(self.store.db.execute("SELECT id FROM jobs WHERE external_id='bad'").fetchone())

    def test_proposal_is_blocked_before_expensive_review_with_bounded_audit(self):
        coordinator = Coordinator.__new__(Coordinator)
        coordinator.store = self.store
        coordinator.config = {}
        coordinator.review_provider = Mock()
        report = {'summary': 'Implementation deferred', 'test_steps': ['Baseline'],
                  'acceptance_evidence': ['x' * 20000]}
        with self.assertRaisesRegex(RuntimeError, 'deferred'):
            coordinator.review(self.store.get(self.job), Path(self.temp.name), 'candidate', report)
        coordinator.review_provider.assert_not_called()
        row = self.store.db.execute("SELECT data FROM evidence WHERE kind='delivery_rejected'").fetchone()
        self.assertIn('Ready', row['data'])
        self.assertIn('Implementation deferred', row['data'])
        self.assertLess(len(row['data']), 18000)

    def test_structured_reviewer_schema_can_deliver_the_required_scope_verdict(self):
        import json
        from kodi.runner import REVIEW_SCHEMA
        from kodi.coordinator import REVIEW_CONTRACT
        from kodi.policy import validate_review
        self.assertFalse(REVIEW_SCHEMA['additionalProperties'])
        self.assertIn('delivery_met', REVIEW_SCHEMA['required'])
        self.assertEqual(REVIEW_SCHEMA['properties']['delivery_met'], {'type': 'boolean'})
        self.assertIn('"delivery_met":true', REVIEW_CONTRACT)
        verdict = validate_review({**self.review, 'summary': 'Source and behavior verified'}, 'openai', 'google')
        self.evidence(review=verdict)
        self.assertTrue(self.store.gate(self.job, 'candidate'))
        for invalid in ('true', 1, None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'Delivery verdict'):
                validate_review({**verdict, 'delivery_met': invalid}, 'openai', 'google')

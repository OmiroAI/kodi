"""Mocked ledger regressions; these do not supply independent acceptance."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator
from kodi.runner import QuotaExhausted

from kodi.store import Store


class FindingResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.store = Store(self.path)
        self.job = self.store.enqueue('example:1', 'system', 'Repair', 'Preserve data')['id']
        self.reject()

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def verdict(self, **fields):
        return dict(approved=True, criteria_met=True, simple=True,
                    blocking_findings=[], author_family='openai', reviewer_family='google',
                    provider='gemini', model='review-model-b', **fields)

    def reject(self):
        review = self.verdict()
        review.update(approved=False, blocking_findings=['Missing dependency'], model='review-model-a')
        self.store.evidence(self.job, 'original', 'review', review)

    def accept(self, sha='original', dispositions=None, model='review-model-b'):
        self.store.evidence(self.job, sha, 'author', {'test_steps': ['Exercise dependency path']})
        self.store.evidence(self.job, sha, 'qa', {'passed': True})
        review = self.verdict(dispositions=dispositions or [])
        review['model'] = model
        self.store.evidence(self.job, sha, 'review', review)
        return self.store.gate(self.job, sha)

    def disposition(self, sha='original', status='source-demonstrated false positive'):
        return dict(finding_id=self.store.findings(self.job)[0]['id'], status=status,
                    candidate_sha=sha, requirements_identity=self.store.requirements_identity(self.job),
                    source_evidence='Manifest declares dependency; source imports match.',
                    test_evidence='Independent offline dependency-path regression passed.')

    def test_identical_sha_conflict_omission_and_retraction_remain_blocked(self):
        self.assertFalse(self.accept())
        self.assertFalse(self.accept(dispositions=[dict(finding_id=self.store.findings(self.job)[0]['id'], status='retracted')]))
        self.assertEqual(len(self.store.findings(self.job)), 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM evidence WHERE kind='review'").fetchone()[0], 3)

    def test_metadata_correction_cannot_clear_source_defect(self):
        self.store.update(self.job, title='Correct routing metadata')
        self.assertFalse(self.accept(dispositions=[self.disposition(status='stale metadata')]))

    def test_repaired_sha_requires_complete_candidate_evidence(self):
        d = self.disposition('repaired', 'fixed with source/test evidence')
        self.assertFalse(self.accept(dispositions=[d]))
        self.assertTrue(self.accept('repaired', [d]))
        self.store.evidence(self.job, 'repaired', 'qa', {'passed': False})
        self.assertFalse(self.store.gate(self.job, 'repaired'))
        self.assertEqual(self.store.findings(self.job)[0]['sha'], 'original')

    def test_missing_dependency_false_positive_needs_independent_evidence(self):
        d = self.disposition()
        self.assertTrue(self.accept(dispositions=[d]))
        d['source_evidence'] = ''
        self.assertFalse(self.accept(dispositions=[d]))

    def test_author_and_original_reviewer_cannot_self_resolve(self):
        d = self.disposition()
        self.assertFalse(self.accept(dispositions=[d], model='review-model-a'))
        review = self.verdict(dispositions=[d])
        review['reviewer_family'] = 'openai'
        self.store.evidence(self.job, 'original', 'review', review)
        self.assertFalse(self.store.gate(self.job, 'original'))
        self.store.evidence(self.job, 'original', 'author', {'test_steps': ['Test'], 'dispositions': [d]})
        self.assertFalse(self.accept())

    def test_restart_requirement_drift_and_budget(self):
        self.assertTrue(self.accept(dispositions=[self.disposition()]))
        for _ in range(3):
            self.store.reserve_recovery(self.job, 'finding reconsideration', 'job', 3)
        self.store.db.close()
        self.store = Store(self.path)
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            self.store.reserve_recovery(self.job, 'finding reconsideration', 'job', 3)
        self.assertEqual(len(self.store.findings(self.job)), 1)
        self.store.update(self.job, requirements='Preserve data and report errors')
        self.assertFalse(self.store.gate(self.job, 'original'))
        self.assertFalse(self.accept())

    def test_conflicting_or_confirmed_dispositions_block(self):
        d = self.disposition()
        self.assertFalse(self.accept(dispositions=[d, d]))
        self.assertFalse(self.accept(dispositions=[self.disposition(status='confirmed defect')]))

    def test_orchestration_budget_separate_facts_and_no_routing(self):
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = dict(reviewer='antigravity', qwen_url='https://example.invalid',
                        qwen_model='example', qwen_env='unused')
        c.review_provider = Mock(side_effect=QuotaExhausted())
        job = self.store.get(self.job)
        self.store.evidence(self.job, 'original', 'qa', {'passed': True, 'log': 'Regression passed'})
        with patch('kodi.coordinator.git', return_value='base'), patch(
                'kodi.coordinator.review_material', return_value=('complete diff', 'source', [])):
            for _ in range(3):
                with self.assertRaisesRegex(RuntimeError, 'no automatic provider routing'):
                    c.review(job, Path('/unused'), 'original', {})
            with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
                c.review(job, Path('/unused'), 'original', {})
        self.assertEqual(c.review_provider.call_count, 3)
        prompt = c.review_provider.call_args.args[3]
        self.assertIn('independent_qa_facts', prompt)
        self.assertIn('Missing dependency', prompt)
        self.assertIn('complete diff', prompt)
        self.assertNotIn('"approved": false', prompt)

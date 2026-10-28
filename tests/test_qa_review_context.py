import json
import tempfile
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
        measured = json.loads(self.store.db.execute(
            "SELECT data FROM evidence WHERE kind='review_context' ORDER BY id DESC LIMIT 1").fetchone()['data'])
        self.assertEqual(measured['prompt_chars'], len(prompts[0]))
        self.assertIn(json.dumps(self.summary()), prompts[0])
        for text in ('Author could not run pytest', 'Keep all requirements', 'COMPLETE_DIFF',
                     'without waiting for downstream CI', 'challenge test adequacy'):
            self.assertIn(text, prompts[0])
        self.assertFalse(self.store.gate(self.job['id'], 'candidate'))
        # Deterministic character measurement, not a claim about reviewer savings.
        addition = '\n\nIndependent QA (validated stored evidence; private metadata withheld):\n' + json.dumps(self.summary())
        self.assertLess(len(addition), 1024)
        self.assertIn('Passing QA alone is insufficient', REVIEW_CONTRACT)

    def test_contracted_review_includes_scope_and_exact_qa(self):
        job = self.store.enqueue('contracted', 'system', 'Implement',
                                 'Delivery type: IMPLEMENTATION\nAdd behavior',
                                 dispatch_stage='Ready')
        self.store.evidence(job['id'], 'candidate', 'qa',
                            dict(sha='candidate', passed=True, suite='system'))
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = {'reviewer': 'gemini'}
        author = dict(summary='Implemented behavior', test_steps=['Check behavior'],
                      delivery_status='implemented', behavioral_evidence=['Regression passes'])
        verdict = dict(approved=True, criteria_met=True, simple=True, blocking_findings=[])
        with patch('kodi.coordinator.git', return_value='base'), patch(
                'kodi.coordinator.review_material', return_value=('DIFF', 'CONTEXT', [])), patch.object(
                c, 'review_provider', return_value=verdict) as provider:
            c.review(job, Path('/unused'), 'candidate', author)
        prompt = provider.call_args.args[3]
        self.assertIn('Frozen delivery contract', prompt)
        self.assertIn(json.dumps(self.store.qa_summary(job['id'], 'candidate')), prompt)
        self.assertIn('Add behavior', prompt)

    def test_review_provider_does_not_consult_acceptance_gate(self):
        self.record(suite='system')
        author = dict(summary='pytest unavailable locally', test_steps=['Check behavior'])
        self.store.evidence(self.job['id'], 'candidate', 'author', author)
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = dict(reviewer='gemini')
        verdict = dict(approved=True, criteria_met=True, simple=True,
                       blocking_findings=[], summary='Technical criteria satisfied')
        # No CI/human evidence exists, and review's own gate is still false.
        self.assertFalse(self.store.gate(self.job['id'], 'candidate'))
        with patch('kodi.coordinator.git', return_value='base'), patch(
                'kodi.coordinator.review_material', return_value=('DIFF', '', [])), patch(
                'kodi.coordinator.run_worker', return_value=(verdict, {}, 0)), patch.object(
                self.store, 'gate', side_effect=AssertionError('Circular acceptance dependency')):
            result = c.review(self.job, Path('/unused'), 'candidate', author)
        self.assertTrue(result['approved'])
        self.assertTrue(self.store.gate(self.job['id'], 'candidate'))
        # Source acceptance does not fabricate downstream approvals.
        kinds = {r['kind'] for r in self.store.db.execute('SELECT kind FROM evidence')}
        self.assertEqual(kinds, {'qa', 'author', 'review', 'review_context'})

    def test_process_reviews_before_acceptance_then_preserves_gate(self):
        # Exercise the orchestration boundary, including the real review provider
        # and gate. Only external workers, Git and publication are simulated.
        for approved in (True, False):
            with self.subTest(approved=approved), tempfile.TemporaryDirectory() as directory:
                job = self.store.enqueue('process-' + str(approved), 'system',
                                         'Fixture', 'Keep all requirements')
                c = Coordinator.__new__(Coordinator)
                c.store = self.store
                c.config = dict(state_dir=directory, reviewer='gemini', max_attempts=1,
                                repos={'system': dict(source=directory, qa_suite='system')})
                author = dict(summary='Author could not run pytest: unavailable',
                              test_steps=['Check behavior'])
                events = []

                def worker(config, checkout, output, prompt, role, provider, **kwargs):
                    if role == 'author':
                        return author, {}, 0
                    events.append('review')
                    self.assertFalse(self.store.gate(job['id'], 'candidate'))
                    self.assertEqual(self.store.get(job['id'])['state'], 'review')
                    self.assertIn('Author could not run pytest', prompt)
                    self.assertIn('"outcome": "passed"', prompt)
                    self.assertIn('Do not report pending downstream CI', prompt)
                    self.assertIn('reasons to reject this review', prompt)
                    return dict(approved=approved, criteria_met=approved, simple=True,
                                blocking_findings=[] if approved else ['Untested semantic defect'],
                                summary='Technical assessment'), {}, 0

                def gate(job_id, sha):
                    events.append('gate')
                    return Store.gate(self.store, job_id, sha)

                def git_result(checkout, *args):
                    if args[:3] == ('diff', '--cached', '--name-only'):
                        return 'example.py\0'
                    return 'candidate'

                with patch('kodi.coordinator.git', side_effect=git_result), patch(
                        'kodi.coordinator.subprocess.run'), patch(
                        'kodi.coordinator.Path.exists', side_effect=[True, False]), patch(
                        'kodi.coordinator.review_material', return_value=('DIFF', '', [])), patch(
                        'kodi.coordinator.qa', return_value=dict(sha='candidate', passed=True, suite='system')), patch(
                        'kodi.coordinator.run_worker', side_effect=worker), patch.object(
                        self.store, 'gate', side_effect=gate), patch.object(
                        c, 'refresh_source'), patch.object(c, 'move'), patch.object(
                        c, 'progress'), patch.object(c, 'notification'), patch.object(c, 'publish') as publish:
                    # The configured source check is read-only; no Git metadata is created.
                    c.process(job)
                self.assertEqual(events, ['review', 'gate', 'gate'])
                self.assertEqual(publish.call_count, int(approved))
                self.assertEqual(self.store.get(job['id'])['state'],
                                 'validated' if approved else 'blocked')
                kinds = {r['kind'] for r in self.store.db.execute(
                    'SELECT kind FROM evidence WHERE job=?', (job['id'],))}
                self.assertEqual(kinds, {'coding_profile', 'author', 'qa', 'review', 'review_context'})
                # Author selection is job-level evidence added before candidate
                # validation; it must coexist with the commit-bound gate records.
                profiles = self.store.db.execute(
                    "SELECT sha,data FROM evidence WHERE job=? AND kind='coding_profile'",
                    (job['id'],)).fetchall()
                self.assertEqual(len(profiles), 1)
                self.assertEqual(profiles[0]['sha'], '')
                self.assertEqual(json.loads(profiles[0]['data']),
                                 dict(profile='legacy', model=None, effort=None))

    def test_prompt_size_record_matches_primary_and_fallback(self):
        self.test_primary_fallback_author_limitations_and_gate_separation()
        rows = self.store.db.execute("SELECT data FROM evidence WHERE kind='review_context'").fetchall()
        self.assertEqual(len(rows), 1)
        size = json.loads(rows[0]['data'])
        self.assertEqual(size['prompt_chars'], size['without_qa_block_chars'] + size['qa_block_chars'])
        self.assertTrue(size['within_budget'])
        self.assertLess(size['qa_block_chars'], 1024)

    def test_budget_rejection_records_size_without_calling_reviewer(self):
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = dict(review_context_max_chars=1)
        with patch('kodi.coordinator.git', return_value='base'), patch(
                'kodi.coordinator.review_material', return_value=('DIFF', '', [])), patch.object(
                c, 'review_provider') as provider:
            with self.assertRaisesRegex(RuntimeError, 'exceeds budget'):
                c.review(self.job, Path('/unused'), 'candidate', {})
        provider.assert_not_called()
        data = json.loads(self.store.db.execute(
            "SELECT data FROM evidence WHERE kind='review_context'").fetchone()['data'])
        self.assertFalse(data['within_budget'])
        self.assertEqual(data['limit_chars'], 1)
        self.assertGreater(data['prompt_chars'], data['qa_block_chars'])

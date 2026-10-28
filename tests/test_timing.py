import json
import unittest

from kodi.store import Store
from kodi.timing import Timing, LIMIT
from kodi.usage import usage_report


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.db.close)
        self.tick = 0
        self.store.timing = Timing(self.store.db, lambda: self.tick, lambda: 1000+self.tick)
        self.job = self.store.enqueue('example:1', 'example', 'Example', 'Test')['id']
        # Most fixtures represent legacy jobs with no phase history.
        with self.store.db:
            self.store.db.execute('DELETE FROM phase_history')

    def observe(self, state, attempt=1, sha=None, owner='coordinator'):
        with self.store.db:
            self.store.timing.observe(self.job, state, attempt, sha, owner)

    def project(self):
        return self.store.delivery_timing(self.job)

    def test_new_job_queue_is_observed_once(self):
        job = self.store.enqueue('example:2', 'example', 'Example', 'Test')['id']
        self.tick = 6
        self.store.enqueue('example:2', 'example', 'Example', 'Test')
        projection = self.store.delivery_timing(job)
        self.assertEqual(projection['seconds']['waiting'], 6)
        self.assertFalse(projection['history_partial'])
        self.assertEqual(len(projection['history']), 1)

    def test_states_replay_retry_and_terminal(self):
        self.store.update(self.job, state='coding', attempt=1)
        self.tick = 5
        self.store.update(self.job, state='coding')
        self.assertEqual(len(self.project()['history']), 1)
        self.observe('retry_backoff')
        self.tick = 8
        self.store.update(self.job, state='coding', attempt=2)
        self.tick = 10
        self.store.update(self.job, state='human_review')
        self.assertEqual(self.project()['actor'], 'reviewer')
        self.tick = 20
        self.store.update(self.job, state='blocked', error='secret-like-raw-error')
        self.assertEqual(self.project()['next_action'], 'inspect')
        self.tick = 24
        self.store.update(self.job, state='done')
        self.tick = 40
        self.store.update(self.job, state='done')
        result = self.project()
        self.assertEqual(result['seconds'], dict(execution=7, waiting=13, blocked=4))
        self.assertEqual(result['coding_retries'], 1)
        self.assertNotIn('secret-like', json.dumps(result))
        self.assertEqual(len(result['history']), 6)

    def test_concurrent_intervals_are_a_union_with_precedence(self):
        self.observe('coding', owner='coding')
        self.observe('human_review', owner='approval')
        self.tick = 5
        self.observe('qa', owner='qa')
        self.tick = 10
        self.observe('done', owner='coding')
        self.tick = 15
        self.observe('done', owner='qa')
        self.tick = 20
        self.observe('done', owner='approval')
        self.assertEqual(self.project()['seconds'], dict(execution=15, waiting=5, blocked=0))
        self.observe('human_review', owner='approval')
        self.observe('blocked', owner='intervention')
        self.tick = 25
        result = self.project()
        self.assertEqual(result['seconds']['blocked'], 5)
        self.assertEqual(set(result['current_blockers']), {'approval', 'intervention'})
        self.assertEqual(result['actor'], 'operator')

    def test_restart_does_not_bill_downtime_and_rollback_is_unknown(self):
        self.observe('coding')
        self.tick = 9
        self.store.timing = Timing(self.store.db, lambda: self.tick, lambda: self.tick)
        self.assertEqual(self.project()['seconds']['execution'], 0)
        self.observe('qa')
        self.tick = 7
        self.observe('review')
        self.assertTrue(self.project()['history_partial'])
        self.assertTrue(all(v >= 0 for v in self.project()['seconds'].values()))
        self.assertIsNone(self.project()['history'][0]['duration'])

    def test_candidate_invalidation_preserves_attempt_identity_and_missing_history(self):
        self.assertIsNone(self.project()['coverage_start'])
        self.assertTrue(self.project()['history_partial'])
        self.store.update(self.job, state='qa', commit_sha='a'*40)
        self.store.evidence(self.job, 'a'*40, 'qa', {'passed': True, 'tests': ['synthetic']})
        self.tick = 4
        self.store.update(self.job, state='qa', commit_sha='b'*40)
        result = self.project()
        self.assertEqual(result['evidence'][0]['validity'], 'candidate_changed')
        self.assertEqual(result['history'][0]['candidate_sha'], 'a'*40)
        self.assertFalse(self.store.gate(self.job, 'b'*40))
        self.assertIn('delivery_timing', usage_report(self.store, 'tasks')[str(self.job)])

    def test_compaction_preserves_totals_and_repeat_identity(self):
        for i in range(300):
            self.tick = i
            self.observe('coding' if i % 2 == 0 else 'qa', attempt=i)
        before = self.project()
        self.assertTrue(before['truncated'])
        self.assertLessEqual(len(before['history']), LIMIT)
        self.assertEqual(before['seconds']['execution'], 299)
        self.observe('qa', attempt=299)
        self.assertEqual(self.project()['seconds'], before['seconds'])

    def test_delivery_wait_to_execution_and_requirements_invalidation(self):
        self.store.update(self.job, state='validated', commit_sha='a'*40)
        self.tick = 3
        self.store.update(self.job, state='publishing')
        self.tick = 5
        result = self.project()
        self.assertEqual(result['seconds'], dict(execution=2, waiting=3, blocked=0))
        self.store.evidence(self.job, 'a'*40, 'qa', {'passed': True})
        self.store.update(self.job, requirements='Changed acceptance criteria')
        self.assertEqual(self.project()['evidence'][0]['validity'], 'requirements_changed')

    def test_compaction_across_overlapping_open_intervals(self):
        self.observe('human_review', owner='approval')
        for i in range(300):
            self.tick = i
            self.observe('coding' if i % 2 == 0 else 'qa', attempt=i)
        self.tick = 310
        self.observe('done')
        self.tick = 315
        self.observe('done', owner='approval')
        self.assertEqual(self.project()['seconds'], dict(execution=310, waiting=5, blocked=0))

    def test_state_and_observation_rollback_together(self):
        original = self.store.timing.observe
        def fail(*args):
            original(*args)
            raise RuntimeError('synthetic failure')
        self.store.timing.observe = fail
        with self.assertRaises(RuntimeError):
            self.store.update(self.job, state='coding')
        self.assertEqual(self.store.get(self.job)['state'], 'queued')
        self.assertIsNone(self.project()['coverage_start'])

    def test_unclaimed_job_projection_does_not_consult_a_candidate_gate(self):
        from unittest.mock import patch
        self.assertIsNone(self.store.get(self.job)['commit_sha'])
        with patch.object(self.store, 'gate', side_effect=AssertionError('No candidate')) as gate:
            projection = self.project()
        gate.assert_not_called()
        self.assertEqual(projection['evidence'], [])
        self.assertTrue(projection['history_partial'])
        self.assertIsNone(projection['worker_seconds'])
        self.assertEqual(self.store.get(self.job)['state'], 'queued')

"""Repository ownership is durable; capacity and acceptance remain separate."""
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from kodi.store import Store


class RepositorySerialization(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'queue.sqlite'
        self.store = Store(self.path)
        self.addCleanup(self.store.db.close)

    def job(self, key, repo='project'):
        return self.store.enqueue(key, repo, key, 'Ready implementation')

    def test_stage_ownership_and_budget_preservation(self):
        owner = self.job('owner')
        self.assertEqual(self.store.claim()['id'], owner['id'])
        waiting = self.job('waiting')
        self.store.update(waiting['id'], attempt=2)
        self.store.evidence(waiting['id'], '', 'retained', {'budget': 2})
        for stage in ('coding', 'qa', 'review', 'validated', 'ci_pending',
                      'ci_failed', 'human_review', 'merge_pending', 'merge_updating', 'blocked'):
            with self.subTest(stage=stage):
                self.store.update(owner['id'], state=stage)
                self.assertEqual(self.store.repository_blocker(waiting)['id'], owner['id'])
                self.assertIsNone(self.store.claim())
        self.store.update(owner['id'], state='cancelled')
        claimed = self.store.claim()
        self.assertEqual((claimed['id'], claimed['attempt']), (waiting['id'], 2))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='retained'").fetchone()[0], 1)

    def test_waiting_repository_does_not_hide_other_repository(self):
        owner = self.job('owner')
        self.store.claim()
        self.store.update(owner['id'], state='human_review')
        first, second = self.job('first'), self.job('second')
        other = self.job('other', 'other')
        self.assertEqual(self.store.claim()['id'], other['id'])
        self.store.update(other['id'], state='done')
        self.store.update(owner['id'], state='done')
        self.assertEqual(self.store.claim()['id'], first['id'])
        self.assertEqual(self.store.repository_blocker(second)['id'], first['id'])

    def test_unstarted_blocked_and_terminal_jobs_do_not_hold(self):
        for state in ('blocked', 'cancelled', 'done', 'intake_pending'):
            job = self.job(state)
            self.store.update(job['id'], state=state)
        waiting = self.job('ready')
        self.assertEqual(self.store.claim()['id'], waiting['id'])

    def test_uncertainty_survives_terminal_and_restart(self):
        owner = self.job('owner')
        self.store.claim()
        self.store.update(owner['id'], state='cancelled')
        self.store.evidence(owner['id'], 'a'*40, 'merge_uncertain', {'operation': 'branch_update'})
        self.job('waiting')
        restarted = Store(self.path)
        self.addCleanup(restarted.db.close)
        self.assertIsNone(restarted.claim())
        self.store.evidence(owner['id'], 'a'*40, 'merge_settled', {})
        self.assertIsNotNone(restarted.claim())

    def test_concurrent_claims_and_restart_keep_single_owner(self):
        first = self.job('first')
        self.job('second')
        def claim():
            store = Store(self.path)
            try:
                row = store.claim()
                return row['id'] if row else None
            finally:
                store.db.close()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertCountEqual(results, [first['id'], None])
        self.store.update(first['id'], state='blocked')
        self.assertIsNone(claim())

    def test_running_cancelled_executor_retains_ownership(self):
        owner = self.job('owner')
        self.store.begin_invocation(owner['id'], 'coding')
        self.store.update(owner['id'], state='cancelled')
        self.job('waiting')
        self.assertIsNone(self.store.claim())

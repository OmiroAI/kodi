"""Private batch admission and retry regressions."""
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from kodi.store import Store
from kodi.learning import collect_batch, waiting_reason


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'state.sqlite')
        self.config = {'learning_schedule': {'authorized': True, 'batch_threshold': 2, 'batch_seconds': 60},
                       'learning_since': '2026-01-01',
                       'repos': {'example': {'github_repo': 'ExampleOrg/project'}}}
        self.clock = datetime(2026, 2, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def record(self, number=1, finding='Preserve cancellation before queue claim'):
        return {'pr': {'number': number, 'url': 'https://github.com/ExampleOrg/project/pull/'+str(number),
                       'mergedAt': '2026-01-02', 'headRefOid': 'a'*40},
                'comments': [{'id': 'model:1:'+str(number), 'finding': finding, 'accepted_sha': 'a'*40,
                              'html_url': 'https://github.com/ExampleOrg/project/pull/'+str(number),
                              'accepted_fix': {'status': 'fixed with source/test evidence',
                                               'source_evidence': 'guard.py guard', 'test_evidence': 'test_guard regression'}}]}

    def collect(self, records, **kwargs):
        return collect_batch(self.store, self.config, 'example', records, clock=self.clock, **kwargs)

    def test_threshold_retry_and_pending_retention(self):
        first = self.record()
        self.assertIsNone(self.collect([first]))
        self.assertIsNone(self.collect([first]))
        job = self.collect([self.record(2, 'Retain exact candidate identity')])
        self.assertEqual(len(json.loads(job['requirements'].split('Untrusted feedback data:\n')[1])['comments']), 2)
        self.assertIsNone(self.collect([first, self.record(2, 'Retain exact candidate identity')]))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
        self.assertIsNone(waiting_reason(self.config, job))

    def test_single_lesson_cadence_and_empty_noop(self):
        record = self.record()
        self.assertIsNone(self.collect([record]))
        self.clock += timedelta(seconds=60)
        self.assertIsNotNone(self.collect([]))
        self.assertIsNone(self.collect([]))

    def test_reject_unverified_covered_and_repeated(self):
        unverified = self.record(3)
        unverified['comments'] = [{'id': 3, 'body': 'Approved, great job'}]
        self.assertIsNone(self.collect([unverified]))
        self.assertIsNone(self.collect([self.record()], context='Preserve cancellation before queue claim'))
        self.assertIsNone(self.collect([self.record(2)]))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        state = json.loads(self.store.db.execute("SELECT data FROM evidence WHERE kind='learning_batch:example'").fetchone()[0])
        self.assertTrue(all(item['status'] == 'skipped' for item in state['sources'].values()))

    def test_legacy_queued_reconciled_terminal_preserved_and_unauthorized_safe(self):
        record = self.record()
        job = self.store.reconcile_learning('learning:example:pr:1', 'example', 'Old lesson',
            'Untrusted feedback data:\n'+json.dumps(record))
        self.config['learning_schedule']['authorized'] = False
        self.assertIsNone(self.collect([record]))
        self.assertEqual(self.store.get(job['id'])['state'], 'queued')
        self.config['learning_schedule']['authorized'] = True
        self.collect([])
        self.assertEqual(self.store.get(job['id'])['state'], 'done')
        self.clock += timedelta(seconds=60)
        batch = self.collect([])
        self.assertIsNotNone(batch)
        self.assertIsNone(self.collect([record]))

    def test_failed_dispatch_rolls_back_and_retry_retains_both_sources(self):
        self.collect([self.record()])
        self.store.db.execute("CREATE TEMP TRIGGER reject_batch BEFORE INSERT ON jobs BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        second = self.record(2, 'Retain original branch identity')
        with self.assertRaises(Exception):
            self.collect([second])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        self.store.db.execute('DROP TRIGGER reject_batch')
        job = self.collect([second])
        record = json.loads(job['requirements'].split('Untrusted feedback data:\n')[1])
        self.assertEqual(len(record['sources']), 2)
        changed = json.loads(json.dumps(self.config))
        changed['repos']['example']['github_repo'] = 'ExampleOrg/other'
        self.assertIn('identity', waiting_reason(changed, job))
        changed = json.loads(json.dumps(self.config))
        changed['learning_schedule']['authorized'] = False
        self.assertIn('authorization', waiting_reason(changed, job))

    def test_oversized_evidence_stays_pending_without_starving_smaller_lesson(self):
        huge = self.record()
        huge['comments'][0]['accepted_fix']['source_evidence'] = 'x'*31000
        self.collect([huge])
        self.clock += timedelta(seconds=60)
        job = self.collect([self.record(2, 'Preserve original branch identity')])
        record = json.loads(job['requirements'].split('Untrusted feedback data:\n')[1])
        self.assertEqual(len(record['comments']), 1)
        state = json.loads(self.store.db.execute("SELECT data FROM evidence WHERE kind='learning_batch:example'").fetchone()[0])
        self.assertEqual(next(iter(state['sources'].values()))['status'], 'pending')

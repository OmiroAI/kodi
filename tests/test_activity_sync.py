import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from kodi.activity import ActivitySync
from kodi.store import Store
from kodi.coordinator import Coordinator


class Receiver:
    def __init__(self):
        self.probes = 0
        self.supported = True
        self.fail = None
        self.receipts = {}
        self.calls = []

    def activity_supported(self, workspace, timeout):
        self.probes += 1
        return self.supported

    def external_activity(self, task, payload, timeout):
        self.calls.append((task, payload))
        key = (payload['source_instance'], payload['event_id'])
        receipt = self.receipts.setdefault(key, {'id':str(len(self.receipts)+1), 'created':False})
        if self.fail:
            raise self.fail
        return receipt


class ActivitySyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'ledger.sqlite'
        self.store = Store(self.path)
        self.addCleanup(lambda:self.store.db.close())
        self.job = self.store.enqueue('example:1', 'project', 'Example', 'Do work', 'task-a')['id']
        self.board = Receiver()

    def usage(self, job=None):
        self.store.usage(job or self.job, 'codex', 'provider default',
                         {'input_tokens':100,'output_tokens':20}, 1, phase='coding', outcome='succeeded')

    def test_lost_ack_restart_replays_same_frozen_payload_and_destination(self):
        self.usage()
        sync = ActivitySync(self.store, self.board)
        self.board.fail = TimeoutError()
        self.assertEqual(sync.sync('workspace')['pending'], 1)
        first = self.board.calls[0]
        self.store.update(self.job, board_id='task-b')
        with self.store.db:
            self.store.db.execute("UPDATE usage SET data='{}'")
            self.store.db.execute('UPDATE activity_outbox SET next_attempt=0')
        self.store.db.close()
        self.store = Store(self.path)
        again = ActivitySync(self.store, self.board)
        self.assertEqual(sync.instance, again.instance)
        self.board.fail = None
        self.assertEqual(again.sync('workspace')['acked'], 1)
        self.assertEqual(first, self.board.calls[-1])
        self.assertEqual(len(self.board.receipts), 1)
        self.assertEqual(again.sync('workspace')['acked'], 1)
        self.assertEqual(len(self.board.calls), 2)

    def test_missing_mapping_waits_without_title_or_sha_matching(self):
        self.store.update(self.job, board_id=None)
        self.usage()
        sync = ActivitySync(self.store, self.board)
        self.assertEqual(sync.sync('workspace')['waiting_mapping'], 1)
        self.assertFalse(self.board.calls)
        self.store.update(self.job, board_id='task-c')
        self.assertEqual(sync.sync('workspace')['acked'], 1)
        self.assertEqual(self.board.calls[0][0], 'task-c')

    def test_unsupported_endpoint_is_cooled_down_without_losing_events(self):
        self.usage()
        self.board.supported = False
        sync = ActivitySync(self.store, self.board)
        for _ in range(3):
            self.assertEqual(sync.sync('workspace')['pending'], 1)
        self.assertEqual(self.board.probes, 1)
        self.assertFalse(self.board.calls)
        with self.store.db:
            self.store.db.execute("DELETE FROM activity_meta WHERE key='probe'")
        self.board.supported = True
        self.assertEqual(sync.sync('workspace')['acked'], 1)

    def test_quarantine_and_fair_retry_do_not_starve_later_observations(self):
        self.usage()
        sync = ActivitySync(self.store, self.board)
        self.board.fail = HTTPError('https://example.invalid', 409, 'private body', None, None)
        self.assertEqual(sync.sync('workspace')['quarantined'], 1)
        self.usage()
        self.board.fail = TimeoutError()
        self.assertEqual(sync.sync('workspace')['pending'], 1)
        self.usage()
        self.board.fail = None
        status = sync.sync('workspace')
        self.assertEqual(status, {'acked':1, 'pending':1, 'quarantined':1})
        self.assertNotIn('private body', json.dumps(status))

    def test_bounded_backfill_counts_distinct_review_rows_and_skips_author(self):
        for _ in range(8):
            self.usage()
        for kind, data in [('author',{}), ('review',{'approved':False,'summary':'Completed review'}),
                           ('review',{'approved':True}), ('qa',{'passed':False,'sha':'a'*40})]:
            self.store.evidence(self.job, 'a'*40, kind, data)
        sync = ActivitySync(self.store, self.board)
        self.assertEqual(sync.sync('workspace', 4)['acked'], 3)
        self.assertEqual(len(self.board.calls), 3)
        sync.sync('workspace', 4)
        sync.sync('workspace', 4)
        sync.sync('workspace', 4)
        self.assertEqual(sync.status(), {'acked':11})
        reviews = [p for _, p in self.board.calls if p['kind']=='review_completed']
        self.assertEqual(len(reviews), 2)
        self.assertNotEqual(reviews[0]['event_id'], reviews[1]['event_id'])

    def test_invalid_record_is_quarantined_but_next_record_advances(self):
        self.usage()
        with self.store.db:
            self.store.db.execute("UPDATE usage SET created='invalid'")
        self.usage()
        sync = ActivitySync(self.store, self.board)
        self.assertEqual(sync.sync('workspace'), {'acked':1, 'quarantined':1})

    def test_dashboard_failure_never_enters_job_failure_handling(self):
        coordinator = Coordinator({'state_dir':self.tmp.name, 'board':{'workspace':'workspace'}})
        self.addCleanup(coordinator.store.db.close)
        with patch('kodi.activity.ActivitySync.sync', side_effect=RuntimeError('private details')):
            self.assertEqual(coordinator.sync_activity(), {'unavailable':True})
        self.assertEqual(self.store.get(self.job)['state'], 'queued')

    def test_historical_usage_migration_defaults_metadata_to_unknown(self):
        import sqlite3
        old = Path(self.tmp.name)/'old.sqlite'
        db = sqlite3.connect(old)
        db.execute('CREATE TABLE usage(id INTEGER PRIMARY KEY, job INTEGER, provider TEXT, model TEXT, data TEXT, seconds REAL, created TEXT)')
        db.execute("INSERT INTO usage VALUES (1,1,'codex','unknown','{}',0,'2026-01-01T00:00:00Z')")
        db.commit(); db.close()
        migrated = Store(old)
        self.addCleanup(migrated.db.close)
        row = migrated.db.execute('SELECT * FROM usage').fetchone()
        self.assertEqual((row['phase'], row['outcome']), ('unknown','unknown'))

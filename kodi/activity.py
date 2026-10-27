"""Durable, bounded export of the local ledger; dashboard outages cannot gate work."""
import json
import time
import uuid

from .activity_payloads import InvalidActivity, usage_event, evidence_event


class ActivitySync:
    def __init__(self, store, board):
        self.db, self.board = store.db, board
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS activity_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activity_outbox (
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, row_id INTEGER NOT NULL,
          job INTEGER NOT NULL, task_id TEXT, payload TEXT,
          state TEXT NOT NULL, reason TEXT, attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt REAL NOT NULL DEFAULT 0, receipt TEXT,
          UNIQUE(source,row_id));
        ''')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO activity_meta VALUES (?,?)',
                            ('instance', str(uuid.uuid4())))
        self.instance = self.db.execute("SELECT value FROM activity_meta WHERE key='instance'").fetchone()[0]

    def prepare(self, limit=100):
        """Advance each source fairly; freeze payload before the first network write."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Activity limit must be between 1 and 100')
        with self.db:
            for source, factory in (('usage', usage_event), ('evidence', evidence_event)):
                key = 'cursor:' + source
                cursor = self.db.execute('SELECT value FROM activity_meta WHERE key=?', (key,)).fetchone()
                rows = self.db.execute(f'SELECT * FROM {source} WHERE id>? ORDER BY id LIMIT ?',
                                       (int(cursor[0]) if cursor else 0, max(1, limit // 2))).fetchall()
                for row in rows:
                    payload, reason = None, None
                    try:
                        payload = factory(dict(row), self.instance)
                    except InvalidActivity as error:
                        reason = str(error)
                    if payload is not None or reason:
                        self.db.execute('''INSERT OR IGNORE INTO activity_outbox
                            (source,row_id,job,payload,state,reason) VALUES (?,?,?,?,?,?)''',
                            (source, row['id'], row['job'], json.dumps(payload) if payload else None,
                             'quarantined' if reason else 'waiting_mapping', reason))
                    self.db.execute('INSERT OR REPLACE INTO activity_meta VALUES (?,?)', (key, str(row['id'])))
            self.db.execute('''UPDATE activity_outbox SET task_id=(SELECT board_id FROM jobs WHERE jobs.id=job),
                state='pending' WHERE state='waiting_mapping' AND task_id IS NULL
                AND EXISTS (SELECT 1 FROM jobs WHERE jobs.id=job AND board_id IS NOT NULL AND board_id!='')''')

    def status(self):
        return {row['state']: row['n'] for row in self.db.execute(
            'SELECT state,COUNT(*) AS n FROM activity_outbox GROUP BY state')}

    def sync(self, workspace, limit=100, budget=2):
        self.prepare(limit)
        deadline = time.monotonic() + min(max(budget, 0), 5)
        now = time.time()
        probe = self.db.execute("SELECT value FROM activity_meta WHERE key='probe'").fetchone()
        supported = False
        if probe:
            state = json.loads(probe[0])
            supported = state['supported'] and state['workspace'] == workspace and state['until'] > now
            if state['workspace'] == workspace and state['until'] > now and not supported:
                return self.status()
        if not supported:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.status()
            try:
                supported = self.board.activity_supported(workspace, remaining)
            except Exception:
                supported = False
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO activity_meta VALUES (?,?)',
                    ('probe', json.dumps({'workspace':workspace, 'supported':supported,
                                         'until':now + (3600 if supported else 300)})))
            if not supported:
                return self.status()
        rows = self.db.execute("SELECT * FROM activity_outbox WHERE state='pending' AND next_attempt<=? ORDER BY next_attempt,id LIMIT ?",
                               (now, limit)).fetchall()
        for row in rows:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                receipt = self.board.external_activity(row['task_id'], json.loads(row['payload']), remaining)
                state, reason = 'acked', None
            except Exception as error:
                code = getattr(error, 'code', None)
                state = 'quarantined' if code in (400, 409) else 'pending'
                reason = 'validation' if code == 400 else 'conflict' if code == 409 else 'delivery_failed'
                receipt = None
                if hasattr(error, 'close'):
                    error.close()
            with self.db:
                self.db.execute('''UPDATE activity_outbox SET state=?,reason=?,receipt=?,attempts=attempts+1,
                    next_attempt=? WHERE id=?''', (state, reason, json.dumps(receipt) if receipt else None,
                    now + min(3600, 30 * 2 ** min(row['attempts'], 7)), row['id']))
        return self.status()

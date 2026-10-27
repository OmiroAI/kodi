"""Persistent queue, independent evidence and a usage ledger."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from .timing import Timing


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, external_id TEXT UNIQUE NOT NULL,
          repo TEXT NOT NULL, title TEXT NOT NULL, requirements TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'queued', attempt INTEGER NOT NULL DEFAULT 0,
          commit_sha TEXT, pr_url TEXT, board_id TEXT, error TEXT,
          created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence (
          id INTEGER PRIMARY KEY, job INTEGER NOT NULL, sha TEXT NOT NULL,
          kind TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS usage (
          id INTEGER PRIMARY KEY, job INTEGER NOT NULL, provider TEXT NOT NULL,
          model TEXT NOT NULL, data TEXT NOT NULL, seconds REAL NOT NULL,
          created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications (
          event TEXT PRIMARY KEY, delivered INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS recovery_attempts (
          job INTEGER NOT NULL, action TEXT NOT NULL, candidate TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(job,action,candidate));
        """)
        # Existing task jobs retain their derived branch name.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in self.db.execute('PRAGMA table_info(usage)')}
            for column in ('phase', 'outcome'):
                if column not in columns:
                    self.db.execute(f"ALTER TABLE usage ADD COLUMN {column} TEXT NOT NULL DEFAULT 'unknown'")
            if 'branch' not in {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}:
                self.db.execute('ALTER TABLE jobs ADD COLUMN branch TEXT')

        self.timing = Timing(self.db)

    def delivery_timing(self, job):
        result = self.timing.project(job)
        current = self.get(job)
        result['coding_retries'] = max(0, current['attempt'] - 1)
        result['recovery_reservations'] = [dict(r) for r in self.db.execute(
            "SELECT action,candidate,attempts FROM recovery_attempts WHERE job=? ORDER BY action,candidate LIMIT 128", (job,))]
        contexts = self.timing.read(job).get('evidence_contexts', {})
        context = hashlib.sha256(current['requirements'].encode()).hexdigest()
        records = self.db.execute("SELECT id,kind,sha,data FROM evidence WHERE job=? ORDER BY id DESC LIMIT 128", (job,)).fetchall()
        result['evidence'] = []
        seen = set()
        gate = self.gate(job, current['commit_sha'])
        for row in records:
            data = json.loads(row['data'])
            key = (row['kind'], row['sha'])
            verdict = data.get('passed' if row['kind'] == 'qa' else 'approved') if isinstance(data, dict) else None
            validity = 'unknown'
            if row['sha'] != current['commit_sha']:
                validity = 'candidate_changed'
            elif key in seen:
                validity = 'superseded'
            elif isinstance(data, dict) and data.get('invalidation') is True:
                validity = 'invalidated'
            elif contexts.get(str(row['id'])) != context:
                validity = 'requirements_changed' if str(row['id']) in contexts else 'unknown'
            elif gate and row['kind'] in ('qa', 'review'):
                validity = 'local_gate_satisfied'
            seen.add(key)
            result['evidence'].append(dict(id=row['id'], kind=row['kind'], candidate_sha=row['sha'],
                                          outcome=verdict if type(verdict) is bool else None,
                                          validity=validity))
        result['worker_seconds'] = self.db.execute(
            "SELECT SUM(seconds) FROM usage WHERE job=?", (job,)).fetchone()[0]
        return result

    def enqueue(self, external_id, repo, title, requirements, board_id=None):
        with self.db:
            inserted = self.db.execute("""INSERT OR IGNORE INTO jobs
              (external_id,repo,title,requirements,board_id,created,updated)
              VALUES (?,?,?,?,?,?,?)""",
              (external_id, repo, title, requirements, board_id, now(), now()))
            if inserted.rowcount:
                job = self.db.execute('SELECT id FROM jobs WHERE external_id=?', (external_id,)).fetchone()[0]
                self.timing.observe(job, 'queued', 0, None, complete=True)
        return self.db.execute("SELECT * FROM jobs WHERE external_id=?", (external_id,)).fetchone()

    def get(self, job):
        return self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()

    def claim(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            active = self.db.execute("SELECT 1 FROM jobs WHERE state IN ('coding','qa','review','publishing','merge_pending','merge_updating')").fetchone()
            row = None if active else self.db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE jobs SET state='coding',updated=? WHERE id=?", (now(), row['id']))
            if row:
                self.timing.observe(row['id'], 'coding', row['attempt'], row['commit_sha'])
            self.db.commit()
            return self.get(row['id']) if row else None
        except Exception:
            self.db.rollback()
            raise

    def update(self, job, **fields):
        allowed = {'state', 'attempt', 'commit_sha', 'pr_url', 'error', 'board_id', 'branch',
                   'title', 'requirements'}
        if not fields or not set(fields) <= allowed:
            raise ValueError("Unsupported job update")
        fields['updated'] = now()
        with self.db:
            self.db.execute("UPDATE jobs SET " + ','.join(k+'=?' for k in fields) + " WHERE id=?",
                            (*fields.values(), job))
            current = self.get(job)
            if current:
                self.timing.observe(job, current['state'], current['attempt'], current['commit_sha'])

    def evidence(self, job, sha, kind, data):
        with self.db:
            inserted = self.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (?,?,?,?,?)",
                            (job, sha, kind, json.dumps(data), now()))
            current = self.get(job)
            if current:
                history = self.timing.read(job)
                contexts = history.setdefault('evidence_contexts', {})
                contexts[str(inserted.lastrowid)] = hashlib.sha256(current['requirements'].encode()).hexdigest()
                history['evidence_contexts'] = dict(list(contexts.items())[-128:])
                self.db.execute('INSERT OR REPLACE INTO phase_history VALUES (?,?)', (job, json.dumps(history)))

    def usage(self, job, provider, model, data, seconds, phase="unknown", outcome="unknown"):
        with self.db:
            self.db.execute("INSERT INTO usage(job,provider,model,data,seconds,created,phase,outcome) VALUES (?,?,?,?,?,?,?,?)",
                            (job, provider, model, json.dumps(data), seconds, now(), phase, outcome))

    def gate(self, job, sha):
        # Only coordinator-created independent records for this exact commit count.
        records = {}
        for row in self.db.execute("SELECT kind,data FROM evidence WHERE job=? AND sha=? ORDER BY id", (job,sha)):
            records[row['kind']] = json.loads(row['data'])
        qa, review, author = (records.get(k, {}) for k in ('qa','review','author'))
        return (qa.get('passed') is True and review.get('approved') is True
                and review.get('criteria_met') is True and review.get('simple') is True
                and review.get('author_family') != review.get('reviewer_family')
                and bool(review.get('author_family')) and bool(review.get('reviewer_family'))
                and not review.get('blocking_findings') and bool(author.get('test_steps')))

    def notify_once(self, event, deliver):
        if self.db.execute("SELECT 1 FROM notifications WHERE event=? AND delivered=1", (event,)).fetchone():
            return False
        deliver()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO notifications(event,delivered) VALUES (?,1)",(event,))
        return True

    def reserve_recovery(self, job, action, candidate, limit):
        """Reserve before work so a restart cannot replenish the recovery budget."""
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO recovery_attempts VALUES (?,?,?,0)',
                            (job, action, candidate))
            result = self.db.execute('UPDATE recovery_attempts SET attempts=attempts+1 '
                'WHERE job=? AND action=? AND candidate=? AND attempts<?',
                (job, action, candidate, limit))
            if not result.rowcount:
                raise RuntimeError('Automatic recovery budget exhausted: '+action)
            return self.db.execute('SELECT attempts FROM recovery_attempts '
                'WHERE job=? AND action=? AND candidate=?', (job, action, candidate)).fetchone()[0]

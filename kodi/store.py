"""Persistent queue, independent evidence and a usage ledger."""
import json
from contextlib import contextmanager
import uuid
import sqlite3
from datetime import datetime, timezone


class StaleClaim(BaseException):
    """Stop stale execution without overwriting the operator state."""


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, invocation=None):
        self.invocation = invocation
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS invocations (
          id TEXT PRIMARY KEY, job INTEGER NOT NULL, stage TEXT NOT NULL,
          candidate TEXT, snapshot TEXT NOT NULL, status TEXT NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS coordination_metrics (
          event TEXT NOT NULL, job INTEGER, candidate TEXT,
          seconds REAL NOT NULL, created TEXT NOT NULL);
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

    def snapshot(self, job):
        row = self.get(job)
        return json.dumps({key: row[key] for key in
            ('state', 'attempt', 'commit_sha', 'requirements', 'branch', 'pr_url')}, sort_keys=True)

    @contextmanager
    def write(self, job):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.invocation:
                row = self.db.execute('SELECT * FROM invocations WHERE id=?',
                                      (self.invocation,)).fetchone()
                if (not row or row['job'] != job or row['status'] != 'running'
                        or row['snapshot'] != self.snapshot(job)):
                    raise StaleClaim()
            yield
            if self.invocation:
                self.db.execute('UPDATE invocations SET snapshot=?,updated=? WHERE id=?',
                    (self.snapshot(job), now(), self.invocation))

    def begin_invocation(self, job, stage):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            # Ownership is per job; queue capacity remains governed by job states.
            # An uncertain invocation prevents redispatch of this job only.
            if self.db.execute("SELECT 1 FROM invocations WHERE job=? AND status='running'",
                               (job,)).fetchone():
                return None
            token = uuid.uuid4().hex
            self.db.execute('INSERT INTO invocations VALUES (?,?,?,?,?,?,?,?)',
                (token, job, stage, self.get(job)['commit_sha'], self.snapshot(job),
                 'running', now(), now()))
            return token

    def metric(self, event, seconds, job=None, candidate=None):
        with self.db:
            self.db.execute('INSERT INTO coordination_metrics VALUES (?,?,?,?,?)',
                            (event, job, candidate, seconds, now()))

    def enqueue(self, external_id, repo, title, requirements, board_id=None):
        with self.db:
            self.db.execute("""INSERT OR IGNORE INTO jobs
              (external_id,repo,title,requirements,board_id,created,updated)
              VALUES (?,?,?,?,?,?,?)""",
              (external_id, repo, title, requirements, board_id, now(), now()))
        return self.db.execute("SELECT * FROM jobs WHERE external_id=?", (external_id,)).fetchone()

    def get(self, job):
        return self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()

    def claim(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            active = self.db.execute("SELECT 1 FROM jobs WHERE state IN "
                "('coding','qa','review','publishing','merge_pending','merge_updating')").fetchone()
            row = None if active else self.db.execute("SELECT * FROM jobs WHERE state='queued' AND NOT EXISTS "
                "(SELECT 1 FROM invocations WHERE job=jobs.id AND status='running') ORDER BY id LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE jobs SET state='coding',updated=? WHERE id=?", (now(), row['id']))
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
        with self.write(job):
            current = self.get(job)
            ownership_changed = any(
                key in fields and fields[key] != current[key]
                for key in ('state', 'attempt', 'commit_sha', 'requirements', 'branch', 'pr_url'))
            self.db.execute("UPDATE jobs SET " + ','.join(k+'=?' for k in fields) + " WHERE id=?",
                            (*fields.values(), job))
            if not self.invocation and ownership_changed:
                # Invalidate ownership changes, including pause then restore state.
                # Metadata refreshes and idempotent control updates retain the claim.
                # Retain running ownership until the actual executor finishes.
                self.db.execute("UPDATE invocations SET snapshot=? WHERE job=? AND status='running'",
                                ('invalidated:'+uuid.uuid4().hex, job))

    def invocation_status(self, job):
        row = self.db.execute('SELECT * FROM invocations WHERE job=? ORDER BY created DESC LIMIT 1',
                              (job,)).fetchone()
        if not row: return None
        record = {key: row[key] for key in ('id', 'stage', 'candidate', 'status', 'updated')}
        try:
            snapshot = json.loads(row['snapshot'])
            record['current_stage'] = snapshot['state']
            record['current_candidate'] = snapshot['commit_sha']
        except (ValueError, KeyError):
            record['invalidated'] = True
        return record

    def evidence(self, job, sha, kind, data):
        with self.write(job):
            self.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (?,?,?,?,?)",
                            (job, sha, kind, json.dumps(data), now()))

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
        with self.write(job):
            self.db.execute('INSERT OR IGNORE INTO recovery_attempts VALUES (?,?,?,0)',
                            (job, action, candidate))
            result = self.db.execute('UPDATE recovery_attempts SET attempts=attempts+1 '
                'WHERE job=? AND action=? AND candidate=? AND attempts<?',
                (job, action, candidate, limit))
            if not result.rowcount:
                raise RuntimeError('Automatic recovery budget exhausted: '+action)
            return self.db.execute('SELECT attempts FROM recovery_attempts '
                'WHERE job=? AND action=? AND candidate=?', (job, action, candidate)).fetchone()[0]

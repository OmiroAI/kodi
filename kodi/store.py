"""Persistent queue, independent evidence and a usage ledger."""
import json
import sqlite3
from datetime import datetime, timezone
from .delivery import requested_type, rejection


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
        CREATE TABLE IF NOT EXISTS clarifications (
          job INTEGER NOT NULL, revision INTEGER NOT NULL,
          input_text TEXT NOT NULL, input_hash TEXT NOT NULL, input_description TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', base_sha TEXT,
          result TEXT, board_description TEXT, answer TEXT,
          answer_event TEXT UNIQUE, telegram_chat INTEGER, telegram_message INTEGER,
          PRIMARY KEY(job,revision));
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
            if 'question_token' not in {row[1] for row in self.db.execute('PRAGMA table_info(clarifications)')}:
                self.db.execute('ALTER TABLE clarifications ADD COLUMN question_token TEXT')
            if 'input_title' not in {row[1] for row in self.db.execute('PRAGMA table_info(clarifications)')}:
                self.db.execute('ALTER TABLE clarifications ADD COLUMN input_title TEXT')
            self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS clarification_question_token ON clarifications(question_token)')

    def enqueue(self, external_id, repo, title, requirements, board_id=None, initial_state='queued', dispatch_stage=None):
        if initial_state not in ('queued','intake_pending'):
            raise ValueError('Unsupported initial job state')
        task_type = requested_type(requirements)
        with self.db:
            self.db.execute("""INSERT OR IGNORE INTO jobs
              (external_id,repo,title,requirements,board_id,created,updated,state)
              VALUES (?,?,?,?,?,?,?,?)""",
              (external_id, repo, title, requirements, board_id, now(), now(), initial_state))
            job = self.db.execute("SELECT * FROM jobs WHERE external_id=?", (external_id,)).fetchone()
            if (dispatch_stage or 'Delivery type:' in requirements) and not self.delivery_contract(job['id']):
                self.evidence(job['id'], '', 'delivery_contract', {
                    'task_type': task_type,
                    'dispatch_stage': dispatch_stage or 'submitted',
                    'requirements': requirements[:24000],
                    'reason': 'Requested scope frozen before worker execution'})
            return job

    def delivery_contract(self, job):
        row = self.db.execute("SELECT data FROM evidence WHERE job=? AND kind='delivery_contract' ORDER BY id LIMIT 1", (job,)).fetchone()
        return json.loads(row['data']) if row else None

    def reconcile_learning(self, external_id, repo, title, requirements, source_url=None):
        """Edits belong to the same source PR, never another task chain.

        Queued jobs can consume the latest input. Preserve active/terminal/paused
        jobs and record changed input for inspection without authorizing a retry.
        """
        with self.db:
            # The context manager commits/rolls back but does not begin a
            # transaction. Lock before reading so reconciliation stays atomic.
            self.db.execute('BEGIN IMMEDIATE')
            job = self.db.execute('SELECT * FROM jobs WHERE external_id=?',
                                  (external_id,)).fetchone()
            # Preserve legacy digest-based jobs rather than starting a new chain.
            if not job:
                candidates = self.db.execute(
                    "SELECT * FROM jobs WHERE repo=? AND title=? AND external_id LIKE 'learning:%' ORDER BY id",
                    (repo, title)).fetchall()
                for candidate in candidates:
                    if source_url is not None:
                        try:
                            source = json.loads(candidate['requirements'].split(
                                'Untrusted feedback data:\n', 1)[1])
                            if source['pr']['url'] != source_url:
                                continue
                        except (IndexError, KeyError, TypeError, ValueError):
                            continue
                    job = candidate
                    break
            if not job:
                self.db.execute("""INSERT INTO jobs
                    (external_id,repo,title,requirements,created,updated)
                    VALUES (?,?,?,?,?,?)""", (external_id, repo, title, requirements, now(), now()))
                job = self.db.execute('SELECT * FROM jobs WHERE external_id=?',
                                      (external_id,)).fetchone()
            previous = self.db.execute(
                "SELECT data FROM evidence WHERE job=? AND kind='learning_feedback' ORDER BY id DESC LIMIT 1",
                (job['id'],)).fetchone()
            latest = json.loads(previous['data']) if previous else job['requirements']
            if latest != requirements:
                self.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (?,'','learning_feedback',?,?)",
                                (job['id'], json.dumps(requirements), now()))
            if job['state'] == 'queued' and job['requirements'] != requirements:
                self.db.execute('UPDATE jobs SET requirements=?,updated=? WHERE id=?',
                                (requirements, now(), job['id']))
            return self.get(job['id'])

    def get(self, job):
        return self.db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()

    def claim(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            active = self.db.execute("SELECT 1 FROM jobs WHERE state IN ('coding','qa','review','publishing','merge_pending','merge_updating')").fetchone()
            row = None if active else self.db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
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
        with self.db:
            self.db.execute("UPDATE jobs SET " + ','.join(k+'=?' for k in fields) + " WHERE id=?",
                            (*fields.values(), job))

    def evidence(self, job, sha, kind, data):
        with self.db:
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
        contract = self.delivery_contract(job)
        if contract and (rejection(contract, author) or review.get('delivery_met') is not True):
            return False
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

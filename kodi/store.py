"""Persistent queue, independent evidence and a usage ledger."""
import hashlib
import json
from contextlib import contextmanager
import uuid
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .delivery import requested_type, rejection


class StaleClaim(BaseException):
    """Stop stale execution without overwriting the operator state."""


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, invocation=None, *, read_only=False):
        self.invocation = invocation
        self.db = (sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
                   if read_only else sqlite3.connect(path))
        self.db.row_factory = sqlite3.Row
        if read_only:
            return
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

    def snapshot(self, job):
        row = self.get(job)
        return json.dumps({key: row[key] for key in
            ('state', 'attempt', 'commit_sha', 'requirements', 'branch', 'pr_url')}, sort_keys=True)

    @contextmanager
    def write(self, job):
        from contextlib import nullcontext
        nested = self.db.in_transaction
        with (nullcontext() if nested else self.db):
            if not nested:
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

    def requirements_identity(self, job):
        return hashlib.sha256(self.get(job)['requirements'].encode()).hexdigest()

    def evidence(self, job, sha, kind, data):
        # Acceptance is requirements-bound; operational snapshots (for example
        # coding profiles) must retain their original payload across restarts.
        if kind in ('author', 'qa', 'review'):
            data = {**data, 'requirements_identity': self.requirements_identity(job)}
        if kind == 'review':
            data['requirements_snapshot'] = self.get(job)['requirements']
        with self.write(job):
            self.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (?,?,?,?,?)",
                            (job, sha, kind, json.dumps(data), now()))

    def findings(self, job):
        """Append-only review records are the ledger; IDs survive process restarts."""
        findings = []
        for row in self.db.execute("SELECT * FROM evidence WHERE job=? AND kind='review' ORDER BY id", (job,)):
            review = json.loads(row['data'])
            for index, finding in enumerate(review.get('blocking_findings', [])):
                findings.append({'id': f"{row['id']}:{index}", 'sha': row['sha'],
                    'requirements_identity': review.get('requirements_identity'),
                    'provider': review.get('provider'), 'model': review.get('model'),
                    'reviewer_family': review.get('reviewer_family'), 'finding': finding})
        return findings

    def review_context(self, job, sha):
        # Supply findings, never a previous approval/summary to copy.
        facts = []
        for row in self.db.execute("SELECT data FROM evidence WHERE job=? AND sha=? AND kind='qa' ORDER BY id", (job, sha)):
            data = json.loads(row['data'])
            if data.get('requirements_identity') == self.requirements_identity(job):
                facts.append(data)
        return {'candidate': sha, 'requirements_identity': self.requirements_identity(job),
                'findings': self.findings(job), 'independent_qa_facts': facts}

    def finding_blockers(self, job, sha, review):
        dispositions = review.get('dispositions', [])
        if not isinstance(dispositions, list):
            return ['Invalid finding dispositions']
        blockers = []
        for finding in self.findings(job):
            matches = [d for d in dispositions if isinstance(d, dict) and d.get('finding_id') == finding['id']]
            reason = 'unresolved dispute; independent source/test disposition required'
            if len(matches) == 1:
                d = matches[0]
                status = d.get('status')
                identities = (review.get('author_family'), review.get('reviewer_family'),
                              review.get('provider'), review.get('model'),
                              finding['reviewer_family'], finding['provider'], finding['model'])
                independent = (all(isinstance(value, str) and value.strip() for value in identities)
                    and review.get('reviewer_family') != review.get('author_family')
                    and review.get('model') and finding['model']
                    and (review.get('reviewer_family'), review.get('model')) !=
                        (finding['reviewer_family'], finding['model']))
                evidenced = (d.get('candidate_sha') == sha and
                    d.get('requirements_identity') == self.requirements_identity(job) and
                    isinstance(d.get('source_evidence'), str) and bool(d['source_evidence'].strip()) and
                    isinstance(d.get('test_evidence'), str) and bool(d['test_evidence'].strip()))
                valid_status = status in ('fixed with source/test evidence', 'source-demonstrated false positive')
                if status == 'fixed with source/test evidence' and finding['sha'] == sha:
                    valid_status = False
                # Metadata dispositions apply only to explicitly classified metadata findings.
                if status == 'stale metadata':
                    valid_status = isinstance(finding['finding'], dict) and finding['finding'].get('category') == 'metadata'
                if independent and evidenced and valid_status:
                    continue
                reason = 'invalid or conflicting disposition: ' + str(status)
            blockers.append(f"Finding {finding['id']}: {reason}")
        return blockers

    def usage(self, job, provider, model, data, seconds, phase="unknown", outcome="unknown"):
        with self.db:
            self.db.execute("INSERT INTO usage(job,provider,model,data,seconds,created,phase,outcome) VALUES (?,?,?,?,?,?,?,?)",
                            (job, provider, model, json.dumps(data), seconds, now(), phase, outcome))

    def candidate_qa(self, job, sha):
        """Latest exact-candidate QA record in the same order used by acceptance."""
        row = self.db.execute(
            "SELECT data FROM evidence WHERE job=? AND sha=? AND kind='qa' ORDER BY id DESC LIMIT 1",
            (job, sha)).fetchone()
        if row is None:
            return 'missing', {}
        try:
            data = json.loads(row['data'])
        except (ValueError, TypeError):
            return 'malformed', {}
        if not isinstance(data, dict) or type(data.get('passed')) is not bool:
            return 'malformed', {}
        if data.get('sha') != sha:
            return 'mismatched', {}
        return ('passed' if data['passed'] else 'failed'), data

    def qa_summary(self, job, sha):
        outcome, data = self.candidate_qa(job, sha)
        # Explicit public vocabulary: never forward arbitrary operator commands,
        # host/image names, logs, paths or other private QA profile contents.
        public = {
            'suite': {'system', 'repository', 'astro'},
            'command': {'python -m unittest discover -s tests -v',
                        'python .kodi/qa.py',
                        'cmp package-lock.json /opt/astro/package-lock.json && cp -a /opt/astro/node_modules /workspace/node_modules && python .kodi/qa.py',
                        'python3 -m unittest discover -s tests -v',
                        'python -m pytest tests -q -p no:cacheprovider'},
            'environment': {'offline container'},
        }
        summary = dict(candidate_sha=sha, outcome=outcome)
        for key, allowed in public.items():
            value = data.get(key)
            summary[key] = value if isinstance(value, str) and value in allowed else 'unknown'
        for key in ('duration_seconds', 'test_count'):
            value = data.get(key)
            valid = (type(value) in (int, float) and 0 <= value < 1e12
                     and (key != 'test_count' or type(value) is int))
            summary[key] = value if valid else 'unknown'
        return summary

    def gate(self, job, sha):
        # Only coordinator-created independent records for this exact commit count.
        records = {}
        for row in self.db.execute("SELECT kind,data FROM evidence WHERE job=? AND sha=? ORDER BY id", (job,sha)):
            records[row['kind']] = json.loads(row['data'])
        qa, review, author = (records.get(k, {}) for k in ('qa','review','author'))
        contract = self.delivery_contract(job)
        if contract and (rejection(contract, author) or review.get('delivery_met') is not True):
            return False
        current = self.requirements_identity(job)
        return (all(record.get('requirements_identity') == current for record in (qa, review, author))
                and not self.finding_blockers(job, sha, review)
                and qa.get('passed') is True and review.get('approved') is True
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

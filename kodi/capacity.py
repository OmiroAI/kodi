"""Durable, opt-in worker admission; expiry never implies worker termination."""
import threading
import sqlite3
import time
import uuid
from contextlib import contextmanager
from .store import StaleClaim


def validate(config):
    value = config.get('capacity')
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'stages', 'providers'}:
        raise ValueError('capacity requires stages and providers')
    if not isinstance(value['stages'], dict) or set(value['stages']) != {'coding', 'qa', 'review'}:
        raise ValueError('capacity stages require coding, qa and review')
    if not isinstance(value['providers'], dict) or not value['providers']:
        raise ValueError('capacity providers must be a nonempty mapping')
    for group in value.values():
        for key, limit in group.items():
            if not isinstance(key, str) or not key or type(limit) is not int or not 1 <= limit <= 64:
                raise ValueError('capacity limits must be integers from 1 to 64')
    return value


def acquire(store, job, stage, provider, limits, owner, expected_state=None):
    """Reserve both dimensions in one write transaction, or reserve neither."""
    if provider not in limits['providers']:
        raise ValueError('Provider has no configured capacity')
    with store.write(job):
        current = store.get(job)
        if current['state'] != (expected_state or stage) or current['state'] in ('paused', 'cancelled', 'queued', 'deferred', 'done'):
            raise StaleClaim()
        if store.db.execute('SELECT 1 FROM capacity_claims WHERE job=? AND released IS NULL', (job,)).fetchone():
            raise RuntimeError('Existing worker capacity claim requires reconciliation')
        occupied = store.db.execute('SELECT * FROM capacity_claims WHERE released IS NULL').fetchall()
        reason = None
        if sum(r['stage'] == stage for r in occupied) >= limits['stages'][stage]:
            reason = 'stage capacity: '+stage
        elif sum(r['provider'] == provider for r in occupied) >= limits['providers'][provider]:
            reason = 'provider capacity: '+provider
        if reason:
            store.db.execute('INSERT INTO capacity_waits VALUES (?,?,?,?) ON CONFLICT(job) DO UPDATE SET reason=excluded.reason',
                             (job, stage, reason, time.time()))
            return None
        generation = store.db.execute('SELECT COALESCE(MAX(generation),0)+1 FROM capacity_claims WHERE job=?', (job,)).fetchone()[0]
        token = uuid.uuid4().hex
        stamp = time.time()
        store.db.execute('INSERT INTO capacity_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                         (token, job, stage, provider, current['attempt'], current['commit_sha'], owner,
                          generation, store.snapshot(job), stamp, stamp, stamp+60))
        store.db.execute('DELETE FROM capacity_waits WHERE job=?', (job,))
        return token


def heartbeat(db, token, owner, snapshot, stamp=None):
    stamp = time.time() if stamp is None else stamp
    with db:
        result = db.execute("""UPDATE capacity_claims SET heartbeat=?,lease=?
            WHERE id=? AND owner=? AND released IS NULL AND snapshot=? AND lease>=?
            AND (NOT EXISTS (SELECT 1 FROM invocations WHERE id=?)
                 OR EXISTS (SELECT 1 FROM invocations WHERE id=? AND status='running' AND snapshot=?))""",
            (stamp, stamp+60, token, owner, snapshot, stamp, owner, owner, snapshot))
        return result.rowcount == 1


@contextmanager
def admission(coordinator, job, stage, provider, expected_state=None):
    limits = validate(getattr(coordinator, 'config', {}))
    if limits is None:
        yield
        return
    store = coordinator.store
    if getattr(coordinator, '_capacity_recovery', None) == stage:
        expected_state = 'blocked'
    owner = store.invocation or uuid.uuid4().hex
    while True:
        current = store.get(job)
        if (current['repo'] not in coordinator.config['repos']
                or coordinator.config['repos'][current['repo']].get('enabled', True) is not True):
            raise StaleClaim()
        if current['board_id'] and coordinator.config.get('board'):
            settings = coordinator.config['board']
            expected = {'coding': 'Coding', 'qa': 'QA', 'review': 'Review', 'validated': 'Review',
                        'merge_updating': 'Human review', 'merge_pending': 'Human review',
                        'human_review': 'Human review', 'blocked': 'Blocked'}[expected_state or stage]
            tasks = [t for t in coordinator.board.tasks(settings) if t['id'] == current['board_id']]
            if (len(tasks) != 1 or tasks[0].get('workflow_step_id') != settings['steps'].get(expected)
                    or (tasks[0].get('description') or '').split('\n\n---\nKodi status:')[0] != current['requirements']):
                raise StaleClaim()
        token = acquire(store, job, stage, provider, limits, owner, expected_state)
        if token:
            break
        time.sleep(.1)
    stopped = threading.Event()
    path = store.db.execute('PRAGMA database_list').fetchone()[2]
    # Capture on the owning connection; heartbeat never shares SQLite connections.
    snapshot = store.snapshot(job)
    def renew():
        db = sqlite3.connect(path)
        try:
            while not stopped.wait(10):
                if not heartbeat(db, token, owner, snapshot):
                    break
        finally:
            db.close()
    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    store.capacity_token = token
    started = completed = False
    try:
        # The invocation snapshot is rechecked at the last local dispatch boundary.
        with store.write(job):
            pass
        started = True
        yield
        completed = True
        with store.write(job):
            claim = store.db.execute('SELECT * FROM capacity_claims WHERE id=?', (token,)).fetchone()
            if claim['lease'] < time.time() or claim['snapshot'] != store.snapshot(job):
                raise StaleClaim()
    except Exception as error:
        from .runner import QuotaExhausted
        # Explicit quota exhaustion is a completed provider response; permit the
        # existing configured fallback after releasing both primary dimensions.
        if isinstance(error, QuotaExhausted):
            completed = True
        raise
    finally:
        store.capacity_token = None
        stopped.set()
        thread.join()
        # Only this executor's observed completion releases capacity. Never reap
        # claims on elapsed time, restart, cancellation or a failed thread start.
        if completed or not started:
            with store.db:
                store.db.execute('UPDATE capacity_claims SET released=? WHERE id=? AND owner=?', (time.time(), token, owner))


def call(coordinator, job, stage, provider, function, expected_state=None):
    with admission(coordinator, job, stage, provider, expected_state):
        return function()


def status(store, job):
    rows = store.db.execute('SELECT id,job,stage,provider,owner,generation,heartbeat,lease FROM capacity_claims WHERE released IS NULL').fetchall()
    wait = store.db.execute('SELECT * FROM capacity_waits WHERE job=?', (job,)).fetchone()
    return {'occupied': [{**dict(r), 'uncertain': r['lease'] < time.time()} for r in rows], 'wait': None if wait is None else
            {'stage': wait['stage'], 'reason': wait['reason'], 'seconds': max(0, time.time()-wait['started'])}}

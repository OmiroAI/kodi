"""Operator controls over retained jobs; never grants publication authority."""
import hashlib
import json
import time
from pathlib import Path


class Paused(BaseException):
    """A worker reached a durable safe boundary."""


def latest(store, job, kind):
    row = store.db.execute('SELECT data FROM evidence WHERE job=? AND kind=? ORDER BY id DESC LIMIT 1',
                           (job, kind)).fetchone()
    return json.loads(row[0]) if row else None


def inspect(store, job):
    row = store.get(job)
    if row is None:
        raise ValueError('Unknown job')
    plan = {key: row[key] for key in ('id', 'external_id', 'repo', 'board_id', 'state',
            'requirements', 'branch', 'commit_sha', 'pr_url', 'attempt')}
    plan['invocations'] = [dict(r) for r in store.db.execute(
        "SELECT id,stage,status,snapshot FROM invocations WHERE job=? ORDER BY created", (job,))]
    plan['capacity'] = [dict(r) for r in store.db.execute(
        'SELECT id,stage,owner FROM capacity_claims WHERE job=? AND released IS NULL', (job,))]
    plan['evidence_ids'] = [r[0] for r in store.db.execute(
        'SELECT id FROM evidence WHERE job=? ORDER BY id', (job,))]
    plan['requests'] = [dict(r) for r in store.db.execute(
        "SELECT id,job,sha,kind,data FROM evidence WHERE job=? AND kind='review_verdict_attempt' "
        "UNION ALL SELECT id,job,sha,kind,data FROM evidence WHERE id=(SELECT MAX(id) FROM evidence WHERE job=0 AND kind='review_request') ORDER BY id", (job,))]
    plan['budgets'] = [dict(r) for r in store.db.execute(
        'SELECT action,candidate,attempts FROM recovery_attempts WHERE job=? ORDER BY action,candidate', (job,))]
    plan['executor'] = latest(store, job, 'executor_identity')
    plan['checkpoint'] = latest(store, job, 'pause_checkpoint')
    plan['priority'] = latest(store, job, 'urgent_recovery')
    owner = store.repository_blocker(row)
    plan['wait'] = {'job': owner['id'], 'reason': 'retained repository ownership'} if owner else None
    if plan['wait'] is None:
        wait = store.db.execute('SELECT stage,reason FROM capacity_waits WHERE job=?', (job,)).fetchone()
        if wait: plan['wait'] = dict(wait)
    plan['digest'] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return plan


def control(store, job, action, digest, reference, dependency=None):
    if not reference.strip():
        raise ValueError('Operator reference required')
    with store.write(job):
        plan = inspect(store, job)
        if digest != plan['digest']:
            raise ValueError('Stale job identity; inspect again')
        row = store.get(job)
        if action == 'pause':
            if row['state'] in ('done', 'cancelled', 'paused', 'publishing', 'merge_pending', 'merge_updating'):
                raise ValueError('Job cannot pause at this stage; reconcile publication first')
            store.evidence(job, row['commit_sha'] or '', 'pause_request', {'reference': reference})
            if not any(r['status'] == 'running' for r in plan['invocations']) and not plan['capacity']:
                checkpoint(store, job, row['state'])
        elif action == 'reconcile':
            reconcile(store, job, plan, reference)
        elif action == 'resume':
            if row['state'] != 'paused' or not plan['checkpoint']:
                raise ValueError('No paused checkpoint')
            if any(r['status'] == 'running' for r in plan['invocations']) or plan['capacity']:
                raise ValueError('Uncertain execution requires reconciliation before resume')
            saved = plan['checkpoint']
            if saved['identity'] != identity(row):
                raise ValueError('Checkpoint identity changed')
            if saved['state'] not in ('queued', 'qa', 'review', 'validated', 'review_wait', 'blocked'):
                raise ValueError('Interrupted coding requires retained-candidate recovery inspection')
            store.evidence(job, row['commit_sha'] or '', 'pause_resumed', {'reference': reference})
            store.update(job, state=saved['state'])
        elif action == 'prioritize':
            if row['state'] != 'queued':
                raise ValueError('Only queued recovery work can be prioritized')
            if dependency is not None:
                owner = store.get(dependency)
                if (not owner or owner['repo'] != row['repo'] or owner['state'] != 'blocked'
                        or dependency == job or uncertain_merge(store, owner['id'])
                        or store.db.execute("SELECT 1 FROM invocations WHERE job=? AND status='running'", (dependency,)).fetchone()
                        or store.db.execute('SELECT 1 FROM capacity_claims WHERE job=? AND released IS NULL', (dependency,)).fetchone()):
                    raise ValueError('Self-repair dependency must be idle blocked work in the same repository')
                dependency = {'job': dependency, 'identity': identity(owner)}
            store.evidence(job, '', 'urgent_recovery', {'reference': reference, 'dependency': dependency, 'requirements': row['requirements'], 'external_id': row['external_id']})
        else:
            raise ValueError('Unknown control action')
        store.evidence(job, row['commit_sha'] or '', 'operator_control',
                       {'action': action, 'digest': digest, 'reference': reference})


def identity(row):
    return {key: row[key] for key in ('external_id', 'repo', 'board_id', 'requirements',
                                      'branch', 'commit_sha', 'pr_url', 'attempt')}


def checkpoint(store, job, state):
    row = store.get(job)
    store.evidence(job, row['commit_sha'] or '', 'pause_checkpoint',
                   {'state': state, 'identity': identity(row)})
    store.update(job, state='paused')


def boundary(store, job, stage):
    with store.write(job):
        requested = store.db.execute("SELECT MAX(id) FROM evidence WHERE job=? AND kind='pause_request'", (job,)).fetchone()[0]
        resumed = store.db.execute("SELECT MAX(id) FROM evidence WHERE job=? AND kind='pause_resumed'", (job,)).fetchone()[0]
        if not requested or (resumed and resumed > requested):
            return
        checkpoint(store, job, stage)
    raise Paused()


def recovery_may_pass(store, owner, job):
    authorization = latest(store, job['id'], 'urgent_recovery')
    dependency = authorization.get('dependency') if authorization else None
    return bool(authorization and authorization.get('requirements') == job['requirements']
                and authorization.get('external_id') == job['external_id'] and dependency and dependency['job'] == owner['id']
                and dependency['identity'] == identity(owner) and owner['state'] == 'blocked'
                and not uncertain_merge(store, owner['id'])
                and not store.db.execute('SELECT 1 FROM capacity_claims WHERE job=? AND released IS NULL', (owner['id'],)).fetchone())


def reconcile(store, job, plan, reference):
    """Operator attests external completion; only a stopped local executor yields."""
    executor = plan['executor']
    running = [r for r in plan['invocations'] if r['status'] == 'running']
    if len(running) != 1 or not executor or executor['token'] != running[0]['id']:
        raise ValueError('Missing exact executor identity; preserve uncertain ownership')
    if running[0]['snapshot'] != store.snapshot(job):
        raise ValueError('Stale executor identity; preserve retained work')
    try:
        current = Path('/proc')/str(executor['pid'])/'stat'
        if current.read_text().split()[21] == executor['start']:
            raise ValueError('Executor process is still alive; reconciliation cannot interrupt service')
    except FileNotFoundError:
        pass
    if store.get(job)['state'] in ('publishing', 'merge_pending', 'merge_updating') or store.delivery_ownership(store.get(job))['active']:
        raise ValueError('Publication requires existing merge-intent reconciliation')
    # A stopped local executor says nothing about a remote request. Require an
    # explicit audit reference for the operator's independent remote inspection.
    if not reference.startswith('remote-completion:') or not reference[len('remote-completion:'):].strip():
        raise ValueError('Remote completion inspection reference required')
    claims = {}
    request = None
    for row in plan['requests']:
        data = json.loads(row['data'])
        if row['kind'] == 'review_verdict_attempt':
            claims[data['token']] = (row, data)
        else:
            request = data
    for row, data in claims.values():
        if data['status'] == 'claimed':
            store.evidence(job, row['sha'], 'review_verdict_attempt',
                           {**data, 'status': 'invalid', 'reconciled': reference})
    if request and request['job'] == job and request['status'] == 'running':
        store.evidence(0, 'qwen', 'review_request', {**request, 'status': 'reconciled', 'reference': reference})
    store.db.execute("UPDATE invocations SET status='reconciled' WHERE id=? AND status='running'", (running[0]['id'],))
    store.db.execute('UPDATE capacity_claims SET released=? WHERE job=? AND released IS NULL', (time.time(), job))
    if store.get(job)['state'] != 'paused':
        # Reconciliation never authorizes another attempt or starts a worker.
        checkpoint(store, job, store.get(job)['state'])


def uncertain_merge(store, job):
    return bool(store.db.execute("SELECT 1 FROM evidence WHERE job=? AND kind='merge_uncertain' AND id>COALESCE((SELECT MAX(id) FROM evidence WHERE job=? AND kind='merge_settled'),0)", (job, job)).fetchone())

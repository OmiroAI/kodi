"""Host-only retirement of one verified idle merge reservation.

No remote mutations. Unsettled requests are deliberately ineligible: a local
lock cannot fence an in-flight GitHub writer. Apply requires two matching reads.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path

from . import github
from .store import now


@contextmanager
def ownership(c, *, readonly=False):
    path = Path(c.config['state_dir'])/'delivery.lock'
    # Preview may not create files. An absent lock requires host initialization.
    with path.open('r' if readonly else 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Delivery ownership is busy') from None
        yield


def _preview(c, job_id, marker):
    job = c.store.get(job_id)
    if not job:
        raise ValueError('Unknown job')
    rows = [dict(r) for r in c.store.db.execute(
        "SELECT id,sha,kind,data FROM evidence WHERE job=? AND kind NOT IN "
        "('merge_recovery_authorization','merge_recovery_result') ORDER BY id", (job_id,))]
    intent = next((r for r in rows if r['id'] == marker and r['kind'] == 'merge_maintenance'), None)
    latest = next((r for r in reversed(rows) if r['kind'] == 'merge_maintenance'), None)
    released = max((r['id'] for r in rows if r['kind'] == 'merge_released'), default=0)
    unsettled = max((r['id'] for r in rows if r['kind'] == 'merge_uncertain'), default=0) > max(
        (r['id'] for r in rows if r['kind'] == 'merge_settled'), default=0)
    plan = dict(job=job_id, marker=marker, sha=job['commit_sha'],
                pr_identity=hashlib.sha256((job['pr_url'] or '').encode()).hexdigest(),
                outcome='uncertain', action='retain_ownership', observation=None, reason='unavailable_or_incomplete')
    try:
        repo = c.config['repos'][job['repo']]['github_repo']
        owner, name = repo.split('/')
        if not intent or intent != latest or marker <= released:
            plan['reason'] = 'marker_absent_replaced_or_released'
            raise ValueError()
        if (not job['commit_sha'] or intent['sha'] != job['commit_sha'] or
                json.loads(intent['data']).get('expected_head') != job['commit_sha']):
            plan['reason'] = 'persisted_identity_mismatch'
            raise ValueError()
        pr = github.pull(job['pr_url'])
        expected_url = f'https://github.com/{repo}/pull/{pr["number"]}'
        if (job['pr_url'].lower() != expected_url.lower() or
                pr['headRefOid'] != job['commit_sha'] or
                pr['headRefName'] != c.job_branch(job) or pr['baseRefName'] != 'main' or
                pr['headRepository']['name'] != name or
                pr['headRepositoryOwner']['login'].lower() != owner.lower()):
            plan['reason'] = 'remote_identity_or_head_mismatch'
            raise ValueError()
        remote = github.remote_head(repo, c.job_branch(job))
        if remote != job['commit_sha']:
            plan['reason'] = 'remote_head_mismatch_or_unavailable'
            raise ValueError()
        # Inspect auto-merge and queue membership separately. Missing fields
        # and malformed lifecycle values never prove absence.
        armed = pr['autoMergeRequest']
        state = pr['state']
        if state not in ('OPEN', 'CLOSED', 'MERGED'):
            raise ValueError('Unknown PR lifecycle')
        queue = github.merge_queue_observation(repo, pr['number'])
        if queue['head'] != remote or queue['state'] != state or type(queue['queued']) is not bool:
            raise ValueError('Merge queue identity changed')
        plan['pr_number'] = pr['number']
        plan['observation'] = dict(state=state, head=remote, armed=armed is not None, queued=queue['queued'],
                                   request_generation=max((r['id'] for r in rows if r['kind'] == 'merge_uncertain'), default=0))
        if armed is not None or queue['queued']:
            plan.update(outcome='active_armed', reason='remote_request_armed')
        elif unsettled or job['state'] in ('publishing', 'merge_updating', 'merge_pending'):
            plan['reason'] = 'unsettled_remote_mutation'
            raise ValueError()
        elif c.store.db.execute("SELECT 1 FROM invocations WHERE job=? AND status='running'", (job_id,)).fetchone():
            plan['reason'] = 'executor_retains_ownership'
            raise ValueError()
        elif state in ('MERGED', 'CLOSED'):
            plan.update(outcome='already_merged' if state == 'MERGED' else 'closed', action='reconcile_terminal', reason='confirmed_terminal_lifecycle')
        elif job['state'] in ('blocked', 'ci_failed'):
            plan.update(outcome='abandoned', action='retire_ownership', reason='matching_idle_intent_no_armed_request')
        else:
            plan.update(outcome='active', reason='job_remains_active')
    except Exception:
        # Never persist raw API responses, exception strings or private paths.
        plan.update(outcome='uncertain', action='retain_ownership')
    identity = dict(plan=plan, job=dict(job), evidence=rows,
                    repository=c.config['repos'][job['repo']]['github_repo'])
    plan['digest'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    plan['observed_at'] = now()
    return plan


def preview(c, job_id, marker):
    with ownership(c, readonly=True):
        return _preview(c, job_id, marker)


def authorize(c, job_id, marker, digest, reference):
    with ownership(c), c.store.write(job_id):
        plan = _preview(c, job_id, marker)
        if (plan['digest'] != digest or plan['action'] == 'retain_ownership' or
                not reference.strip()):
            raise RuntimeError('Current actionable preview and scoped operator reference required')
        c.store.evidence(job_id, plan['sha'], 'merge_recovery_authorization',
                         dict(marker=marker, digest=digest, reference=reference, action=plan['action'], preview=plan))


def apply(c, job_id, marker, digest):
    with ownership(c), c.store.write(job_id):
        for row in c.store.db.execute("SELECT data FROM evidence WHERE job=? AND kind='merge_recovery_result'", (job_id,)):
            result = json.loads(row['data'])
            if result['marker'] == marker and result['digest'] == digest:
                return result
        auth = c.store.db.execute("SELECT id,data FROM evidence WHERE job=? AND kind='merge_recovery_authorization' ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        if not auth or any(json.loads(auth['data']).get(k) != v for k, v in dict(marker=marker, digest=digest).items()):
            raise RuntimeError('Scoped operator authorization is absent or mismatched')
        plan = _preview(c, job_id, marker)
        # A second remote read catches a request armed during reconciliation.
        confirmed = _preview(c, job_id, marker)
        if (plan['digest'] != digest or confirmed['digest'] != digest or
                plan['action'] == 'retain_ownership'):
            raise RuntimeError('Preview changed or remote intent remains uncertain/active')
        job = c.store.get(job_id)
        result = dict(job=job_id, sha=job['commit_sha'], marker=marker, digest=digest,
                      authorization_id=auth['id'], action=plan['action'], observation=plan['observation'],
                      observed_at=plan['observed_at'], prior_ownership=True, resulting_ownership=False)
        if plan['action'] == 'reconcile_terminal':
            # Existing lifecycle delivery retries its deduplicated notifications
            # on the next normal tick, outside this atomic local transaction.
            c.finish_pr(job, plan['observation']['state'], deliver=False)
        c.store.evidence(job_id, job['commit_sha'], 'merge_released', result)
        c.store.evidence(job_id, job['commit_sha'], 'merge_recovery_result', result)
        return result

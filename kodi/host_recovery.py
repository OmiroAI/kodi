"""Host-only, commit-scoped recovery. No publication or scheduling side effects."""
from .learning import tracking_requirements
import fcntl
import hashlib
import json
from pathlib import Path

from . import github
from .runner import git as runner_git, qa


def git(checkout, *args):
    return runner_git(checkout, '--no-optional-locks', *args)


def record_stage(c, job, stage, base=None):
    checkout = Path(c.config['state_dir'])/'jobs'/str(job['id'])/'checkout'
    c.store.evidence(job['id'], job['commit_sha'], 'dispatch', {
        'stage': stage, 'requirements': job['requirements'],
        'base': base or git(checkout, 'rev-parse', 'main'), 'board_id': job['board_id'],
        'external_id': job['external_id'], 'branch': c.job_branch(job)})


def latest(c, job, kind):
    row = c.store.db.execute('SELECT data FROM evidence WHERE job=? AND sha=? AND kind=? ORDER BY id DESC LIMIT 1',
                             (job['id'], job['commit_sha'], kind)).fetchone()
    return json.loads(row['data']) if row else None


def preview(c, job_id, stage, *, waiting=False):
    job = c.store.get(job_id)
    if not job:
        raise ValueError('Unknown job')
    blockers = []
    if not job['commit_sha']:
        blockers.append('Missing retained candidate SHA; coding-stage recovery requires operator inspection')
    dispatch = latest(c, job, 'dispatch')
    if job['state'] not in (('review', 'review_wait') if waiting else ('blocked',)): blockers.append('Only retained blocked work is recoverable')
    if not dispatch or dispatch.get('stage') != stage:
        blockers.append('Missing failed-stage dispatch provenance; operator inspection required')
    if dispatch and (dispatch['requirements'] != job['requirements'] or
                     dispatch['board_id'] != job['board_id'] or dispatch['external_id'] != job['external_id']):
        blockers.append('Stale requirements or identity; fresh acceptance required')
    repo = c.config['repos'].get(job['repo'], {})
    if repo.get('enabled') is not True: blockers.append('Repository is not enabled')
    checkout = Path(c.config['state_dir'])/'jobs'/str(job_id)/'checkout'
    try:
        if git(checkout, 'status', '--porcelain', '--untracked-files=all'):
            blockers.append('Dirty checkout; preserve work for operator inspection')
        if git(checkout, 'rev-parse', 'HEAD') != job['commit_sha']:
            blockers.append('Candidate SHA changed')
        base = git(checkout, 'rev-parse', 'main')
        if not dispatch or base != dispatch['base'] or git(Path(repo['source']), 'rev-parse', 'main') != base:
            blockers.append('Stale base; fresh validation required')
        if git(checkout, 'symbolic-ref', '--short', 'HEAD') != c.job_branch(job):
            blockers.append('Checkout branch changed')
    except Exception:
        blockers.append('Checkout/base unavailable; operator inspection required')
        base = None
    try:
        if github.remote_head(repo['github_repo'], 'main') != base:
            blockers.append('Remote base changed; fresh validation required')
        remote = github.remote_head(repo['github_repo'], c.job_branch(job))
        if job['pr_url']:
            from .submission import validate_pull
            validate_pull(repo['github_repo'], github.pull(job['pr_url']), c.job_branch(job), job['commit_sha'])
            if remote != job['commit_sha']: raise ValueError('head drift')
        elif remote:
            raise ValueError('untracked publication')
    except Exception:
        blockers.append('Unknown remote mutation; reconcile branch and PR before retry')
        remote = None
    budgets = [dict(r) for r in c.store.db.execute('SELECT action,candidate,attempts FROM recovery_attempts WHERE job=?', (job_id,))]
    if any(r['attempts'] >= 2 and r['candidate'] == job['commit_sha'] and r['action'] in (stage, 'host-'+stage) for r in budgets):
        blockers.append('Recovery budget exhausted')
    if not latest(c, job, 'author'): blockers.append('Missing author artifacts')
    if stage == 'review' and not (latest(c, job, 'qa') or {}).get('passed'):
        blockers.append('Exact-commit QA is required before review')
    plan = {'job': job_id, 'board_id': job['board_id'], 'external_id': job['external_id'],
            'repo': job['repo'], 'pr_url': job['pr_url'], 'sha': job['commit_sha'],
            'requirements': job['requirements'],
            'branch': c.job_branch(job), 'remote_head': remote, 'base': base, 'stage': stage,
            'attempt': job['attempt'], 'budgets': budgets, 'dispatch': dispatch,
            'evidence_ids': [r[0] for r in c.store.db.execute('SELECT id FROM evidence WHERE job=? AND kind NOT IN (\'recovery_authorization\',\'recovery_claim\',\'recovery_result\') ORDER BY id', (job_id,))]}
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    # Inventory current requirements, but omit raw dispatch and worker artifacts.
    plan.pop('dispatch')
    inspections = (['Authoritative task state and requirements must be checked during authorization and apply']
                   if job['board_id'] else [])
    plan.update(digest=digest, blockers=blockers, inspections=inspections,
                resumable=not blockers and not inspections)
    return plan


def validate_authoritative_task(c, job):
    """Live scheduling check belongs to authorization/claim, never preview."""
    if not job['board_id']:
        return
    settings = c.config.get('board', {})
    tasks = [t for t in c.board.tasks(settings) if t['id'] == job['board_id']]
    if len(tasks) != 1 or tasks[0].get('workflow_step_id') != settings.get('steps', {}).get('Blocked'):
        raise RuntimeError('Authoritative task is not Blocked; paused/deferred work cannot resume')
    if (tasks[0].get('description') or '').split('\n\n---\nKodi status:')[0] != tracking_requirements(job):
        raise RuntimeError('Authoritative requirements changed')


def authorize(c, job_id, stage, digest, reference):
    plan = preview(c, job_id, stage)
    if plan['blockers'] or plan['digest'] != digest or not reference.strip():
        raise RuntimeError('Inspect a current unblocked preview and supply a scoped owner reference')
    validate_authoritative_task(c, c.store.get(job_id))
    c.store.evidence(job_id, plan['sha'], 'recovery_authorization',
                     {'digest': digest, 'stage': stage, 'reference': reference})


def apply(c, job_id, stage, digest):
    with (Path(c.config['state_dir'])/'delivery.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Delivery slot is owned; retry later') from None
        job = c.store.get(job_id)
        claim = latest(c, job, 'recovery_claim')
        if claim and claim.get('digest') == digest:
            return {'job': job_id, 'state': job['state'], 'claimed': True,
                    'inspection_required': (latest(c, job, 'recovery_result') or {}).get('digest') != digest}
        active = c.store.db.execute("SELECT 1 FROM jobs WHERE state IN ('coding','qa','review','publishing','merge_pending','merge_updating') LIMIT 1").fetchone()
        if active:
            raise RuntimeError('Another delivery owns the serialized slot; inspect before retry')
        plan = preview(c, job_id, stage)
        auth = latest(c, job, 'recovery_authorization')
        if plan['blockers'] or plan['digest'] != digest or not auth or auth['digest'] != digest or auth['stage'] != stage:
            raise RuntimeError('Recovery blocked: '+('; '.join(plan['blockers']) or 'scoped authorization is stale or absent'))
        validate_authoritative_task(c, job)
        from .delayed_review import latest as delayed_state
        pending = delayed_state(c.store, job_id) if stage == 'review' else None
        if pending and pending['status'] != 'complete':
            raise RuntimeError('Delayed review retains its budget; reconcile through the scheduler')
        c.store.reserve_recovery(job_id, 'host-'+stage, job['commit_sha'], 2)
        # Claim is durable before invocation. Crash uncertainty requires inspection,
        # never automatic replay, even if the process is restarted.
        c.store.evidence(job_id, job['commit_sha'], 'recovery_claim', {'digest': digest, 'stage': stage})
        checkout = Path(c.config['state_dir'])/'jobs'/str(job_id)/'checkout'
        try:
            from . import capacity
            c._capacity_recovery = stage
            if stage == 'qa':
                result = capacity.call(c, job_id, 'qa', 'qa',
                    lambda: qa(c.config, checkout, job['commit_sha'], c.config['repos'][job['repo']]['qa_suite']))
                c.store.evidence(job_id, job['commit_sha'], 'qa', result)
                if not result.get('passed'): raise RuntimeError('QA failed; inspect retained evidence')
            else:
                c.review(job, checkout, job['commit_sha'], latest(c, job, 'author'))
            accepted = c.store.gate(job_id, job['commit_sha'])
            if stage != 'qa' and not accepted: raise RuntimeError('Review did not pass')
            c.store.update(job_id, state='validated' if accepted else 'blocked',
                           error=None if accepted else 'QA recovered; review requires separate authorization')
            if stage == 'qa' and not accepted:
                record_stage(c, c.store.get(job_id), 'review')
            c.store.evidence(job_id, job['commit_sha'], 'recovery_result', {'digest': digest, 'passed': True})
        except Exception:
            c.store.update(job_id, state='blocked', error='Recovery failed; inspect retained artifacts')
            c.store.evidence(job_id, job['commit_sha'], 'recovery_result', {'digest': digest, 'passed': False})
            raise
        finally:
            c._capacity_recovery = None
        c.progress(c.store.get(job_id))
        return {'job': job_id, 'state': c.store.get(job_id)['state']}


def track(c, job_id):
    """Refresh common task tracking without changing scheduling or authorization."""
    with (Path(c.config['state_dir'])/'delivery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        job = c.store.get(job_id)
        if not job:
            raise ValueError('Unknown job')
        settings = c.config['board']
        if job['board_id']:
            matches = [t for t in c.board.tasks(settings) if t['id'] == job['board_id']]
            if len(matches) != 1:
                raise RuntimeError('Tracking task missing or ambiguous; inspect board identity before retry')
            if (matches[0].get('description') or '').split('\n\n---\nKodi status:')[0] != tracking_requirements(job):
                raise RuntimeError('Authoritative requirements changed; reconcile before refreshing tracking')
            c.progress(job)
            return job['board_id']
        ensure_internal_tracking(c, job)
        c.progress(c.store.get(job_id))
        return c.store.get(job_id)['board_id']


def ensure_internal_tracking(c, job):
    """Serialize tracking creation across control and delivery workers."""
    with (Path(c.config['state_dir'])/'board-tracking.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = c.store.get(job['id'])
        if job['board_id']:
            return job['board_id']
        return _create_internal_tracking(c, job)


def _create_internal_tracking(c, job):
    job_id = job['id']
    settings = c.config['board']
    identity = 'kodi-internal:'+str(job_id)
    matches = [t for t in c.board.tasks(settings) if t.get('external_id') == identity]
    if len(matches) > 1: raise RuntimeError('Ambiguous tracking tasks; inspect before retry')
    task = matches[0] if matches else c.board.create(settings, job['title'], tracking_requirements(job), identity)
    if (task.get('external_id') != identity or
            (task.get('description') or '').split('\n\n---\nKodi status:')[0] != tracking_requirements(job)):
        raise RuntimeError('Tracking task identity or authoritative requirements changed; inspect before linking')
    backlog = settings.get('steps', {}).get('Backlog')
    if not matches and backlog and task.get('workflow_step_id') != backlog:
        raise RuntimeError('New tracking task is not in Backlog; inspect scheduling before linking')
    c.store.update(job_id, board_id=task['id'])
    return task['id']

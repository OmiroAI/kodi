"""Host-only, commit-scoped recovery. No publication or scheduling side effects."""
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


def preview(c, job_id, stage):
    job = c.store.get(job_id)
    if not job:
        raise ValueError('Unknown job')
    blockers = []
    dispatch = latest(c, job, 'dispatch')
    if job['state'] != 'blocked': blockers.append('Only retained blocked work is recoverable')
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
    if job['board_id']:
        settings = c.config.get('board', {})
        task = next((t for t in c.board.tasks(settings) if t['id'] == job['board_id']), None)
        if not task or task.get('workflow_step_id') != settings.get('steps', {}).get('Blocked'):
            blockers.append('Authoritative task is not Blocked; paused/deferred work cannot resume')
        elif (task.get('description') or '').split('\n\n---\nKodi status:')[0] != job['requirements']:
            blockers.append('Authoritative requirements changed')
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
            'branch': c.job_branch(job), 'remote_head': remote, 'base': base, 'stage': stage,
            'attempt': job['attempt'], 'budgets': budgets, 'dispatch': dispatch,
            'evidence_ids': [r[0] for r in c.store.db.execute('SELECT id FROM evidence WHERE job=? AND kind NOT IN (\'recovery_authorization\',\'recovery_claim\',\'recovery_result\') ORDER BY id', (job_id,))]}
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    # Do not expose requirements, local paths, logs or worker output in the plan.
    plan.pop('dispatch')
    plan.update(digest=digest, blockers=blockers, resumable=not blockers)
    return plan


def authorize(c, job_id, stage, digest, reference):
    plan = preview(c, job_id, stage)
    if plan['blockers'] or plan['digest'] != digest or not reference.strip():
        raise RuntimeError('Inspect a current unblocked preview and supply a scoped owner reference')
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
        c.store.reserve_recovery(job_id, 'host-'+stage, job['commit_sha'], 2)
        # Claim is durable before invocation. Crash uncertainty requires inspection,
        # never automatic replay, even if the process is restarted.
        c.store.evidence(job_id, job['commit_sha'], 'recovery_claim', {'digest': digest, 'stage': stage})
        checkout = Path(c.config['state_dir'])/'jobs'/str(job_id)/'checkout'
        try:
            if stage == 'qa':
                result = qa(c.config, checkout, job['commit_sha'], c.config['repos'][job['repo']]['qa_suite'])
                c.store.evidence(job_id, job['commit_sha'], 'qa', result)
                if not result.get('passed'): raise RuntimeError('QA failed; inspect retained evidence')
            else:
                c.review(job, checkout, job['commit_sha'], latest(c, job, 'author'))
                if not c.store.gate(job_id, job['commit_sha']): raise RuntimeError('Review did not pass')
            c.store.update(job_id, state='validated' if c.store.gate(job_id, job['commit_sha']) else 'blocked',
                           error=None if c.store.gate(job_id, job['commit_sha']) else 'QA recovered; review requires separate authorization')
            if stage == 'qa' and not c.store.gate(job_id, job['commit_sha']):
                record_stage(c, c.store.get(job_id), 'review')
            c.store.evidence(job_id, job['commit_sha'], 'recovery_result', {'digest': digest, 'passed': True})
        except Exception:
            c.store.update(job_id, state='blocked', error='Recovery failed; inspect retained artifacts')
            c.store.evidence(job_id, job['commit_sha'], 'recovery_result', {'digest': digest, 'passed': False})
            raise
        c.progress(c.store.get(job_id))
        return {'job': job_id, 'state': c.store.get(job_id)['state']}


def track(c, job_id):
    """Mirror internal work using the existing task API; never move it to Ready."""
    with (Path(c.config['state_dir'])/'delivery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        job = c.store.get(job_id)
        if job['board_id']: return job['board_id']
        settings = c.config['board']
        identity = 'kodi-internal:'+str(job_id)
        matches = [t for t in c.board.tasks(settings) if t.get('external_id') == identity]
        if len(matches) > 1: raise RuntimeError('Ambiguous tracking tasks; inspect before retry')
        task = matches[0] if matches else c.board.create(settings, job['title'], job['requirements'], identity)
        c.store.update(job_id, board_id=task['id'])
        c.progress(c.store.get(job_id))
        return task['id']

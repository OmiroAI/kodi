"""Recoverable dashboard discard, using the existing execution fences."""
import json

from . import github
from .job_control import identity, latest


def settle_merge(store, job, repo):
    """Disarm existing remote requests; uncertainty keeps restoration blocked."""
    if not job['pr_url']:
        return
    pr = github.pull(job['pr_url'])
    if pr['state'] not in ('OPEN', 'MERGED', 'CLOSED'):
        raise ValueError('Remote PR lifecycle unavailable')
    if pr['state'] == 'OPEN':
        if 'autoMergeRequest' not in pr:
            raise ValueError('Remote merge state unavailable; ticket remains discarded')
        if pr['autoMergeRequest'] is not None:
            github.gh('pr', 'merge', job['pr_url'], '--disable-auto')
        observed = github.merge_queue_observation(repo, pr['number'])
        if observed['queued']:
            query = 'query($url:URI!){resource(url:$url){... on PullRequest{id}}}'
            result = json.loads(github.gh('api', 'graphql', '-f', 'query='+query, '-f', 'url='+job['pr_url']))
            node = result['data']['resource']['id']
            mutation = 'mutation($id:ID!){dequeuePullRequest(input:{pullRequestId:$id}){clientMutationId}}'
            result = json.loads(github.gh('api', 'graphql', '-f', 'query='+mutation, '-f', 'id='+node))
            if result.get('errors'):
                raise ValueError('Remote queue removal failed; ticket remains discarded')
        pr = github.pull(job['pr_url'])
        observed = github.merge_queue_observation(repo, pr['number'])
        if pr.get('autoMergeRequest', True) is not None or observed['queued']:
            raise ValueError('Remote merge request remains; ticket remains discarded')
    store.evidence(job['id'], job['commit_sha'] or '', 'discard_merge_settled', {'state': pr['state']})


def transition(c, task_id, action):
    store = c.store
    if action == 'restore':
        pending = store.db.execute('SELECT * FROM jobs WHERE board_id=? ORDER BY id DESC LIMIT 1', (task_id,)).fetchone()
        if pending and pending['state'] == 'discarded' and pending['pr_url']:
            try:
                settle_merge(store, pending, c.config['repos'][pending['repo']]['github_repo'])
            except Exception:
                raise ValueError('Remote merge state uncertain; ticket remains discarded') from None
    with store.write(0):
        row = store.db.execute('SELECT data FROM dashboard_tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('Task not found')
        task = json.loads(row['data'])
        job = store.db.execute('SELECT * FROM jobs WHERE board_id=? ORDER BY id DESC LIMIT 1', (task_id,)).fetchone()
        discarded = task.get('discarded', False)
        if action == 'discard':
            if not discarded:
                task['discarded'] = True
                task['discarded_stage'] = task['workflow_step_id']
                task['workflow_step_id'] = c.config['board']['steps']['Discarded']
                if job:
                    # Invalidates running invocation/capacity snapshots without
                    # pretending that external execution has terminated.
                    store.update(job['id'], state='discarded')
                    store.evidence(job['id'], job['commit_sha'] or '', 'discard_checkpoint',
                                   {'state': job['state'], 'identity': identity(job), 'error': job['error'],
                                    'invocations': [dict(r) for r in store.db.execute(
                                        "SELECT id,snapshot FROM invocations WHERE job=? AND status='running'", (job['id'],))]})
        elif action == 'restore':
            if not discarded:
                raise ValueError('Ticket is not discarded')
            if job:
                if store.db.execute("SELECT 1 FROM invocations WHERE job=? AND status='running'", (job['id'],)).fetchone() or store.db.execute('SELECT 1 FROM capacity_claims WHERE job=? AND released IS NULL', (job['id'],)).fetchone():
                    raise ValueError('Execution remains active or uncertain; retain discarded ticket until reconciled')
                saved = latest(store, job['id'], 'discard_checkpoint')
                if not saved or saved['identity'] != identity(job):
                    raise ValueError('Discarded identity changed; inspect retained work')
                restore_job(store, job, saved)
            task['discarded'] = False
            task['workflow_step_id'] = task.pop('discarded_stage')
        else:
            raise ValueError('Unsupported ticket action')
        store.db.execute('UPDATE dashboard_tasks SET data=? WHERE id=?', (json.dumps(task), task_id))
    if action == 'discard' and job and job['pr_url']:
        try:
            settle_merge(store, job, c.config['repos'][job['repo']]['github_repo'])
        except Exception:
            store.update(job['id'], error='Discarded locally; remote merge cancellation needs retry. Restore is blocked until verified.')
            raise ValueError('Discarded locally; remote merge cancellation could not be verified. Retry Discard before restoring.') from None
    return {'ok': True}


def restore_job(store, job, saved):
    # In-flight stages and publication resume through explicit retained-candidate
    # recovery, never by replaying an interrupted mutation or resetting budgets.
    state = saved['state']
    if state in ('qa', 'review'):
        store.evidence(job['id'], job['commit_sha'] or '', 'pause_checkpoint',
                       {'state': state, 'identity': identity(job)})
        state = 'paused'
    if state not in ('queued', 'intake_pending', 'qa', 'review', 'review_wait', 'validated',
                     'ci_pending', 'ci_failed', 'human_review', 'needs_input', 'paused', 'done', 'cancelled', 'blocked'):
        state = 'blocked'
    store.evidence(job['id'], job['commit_sha'] or '', 'ticket_restored', {'state': state})
    store.update(job['id'], state=state, error='Inspect interrupted work before recovery' if state == 'blocked' else saved.get('error'))

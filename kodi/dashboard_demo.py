"""Disposable fictional dashboard data. Never opens the operator's configured ledger."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .coordinator import Coordinator


def seed(state_dir):
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    config = {'state_dir': str(state_dir), 'board_backend': 'native', '_dashboard_demo': True,
              'capacity': {'stages': {'coding': 2, 'qa': 1, 'review': 1},
                           'providers': {'codex': 2, 'qa': 1, 'antigravity': 1, 'qwen': 1}},
              'repos': {key: {'enabled': key != 'archive', 'auto_merge': key in ('core', 'web'),
                              'github_repo': 'ExampleOrg/'+key} for key in ('core','web','api','docs','archive')}}
    c = Coordinator(config)
    samples = [
        ('web','Refresh the account settings layout','Backlog',None),
        ('api','Add a compact response for project summaries','Backlog',None),
        ('docs','Clarify the first-run setup guide','Backlog',None),
        ('core','Keep retries attached to the original job','Ready','queued'),
        ('web','Make ticket search work across repositories','Ready','queued'),
        ('core','Surface the next action for waiting jobs','Coding','coding'),
        ('api','Reduce repeated reads in the activity endpoint','QA','qa'),
        ('web','Preserve ticket content after editing','Review','review'),
        ('web','Improve empty states in the task list','Human review','human_review'),
        ('api','Keep request activity inside its reporting window','Human review','human_review'),
        ('docs','Document the supported report boundaries','Needs input','needs_input'),
        ('core','Allow a bounded deadline for large checkouts','Blocked','blocked'),
        ('web','Normalize usage filters before pagination','Done','done'),
    ]
    now = datetime.now(timezone.utc)
    jobs = []
    for index, (repo, title, stage, phase) in enumerate(samples):
        text = 'Repo: '+repo+'\n\nGoal: '+title+'.\n\nAcceptance criteria:\n- The requested behavior works consistently.\n- Existing authorization and acceptance gates remain intact.'
        task = c.board.create(config['board'], title, text, 'demo:'+str(index), config['board']['repository_ids'][repo])
        c.board.move(config['board'], task['id'], stage)
        if phase:
            job = c.store.enqueue('demo:'+str(index), repo, title, text, task['id'])
            sha = format(index+1, '040x') if phase not in ('queued','needs_input','blocked') else None
            error = ('Checkout deadline exceeded. An inspected recovery is required.' if phase == 'blocked'
                     else 'Which reporting boundary should the new report use?' if phase == 'needs_input' else None)
            pr = 'https://github.com/ExampleOrg/'+repo+'/pull/'+str(index+20) if phase in ('human_review','done') else None
            c.store.update(job['id'], state=phase, attempt=int(phase not in ('queued','needs_input','blocked')),
                           commit_sha=sha, error=error, pr_url=pr)
            jobs.append(job['id'])
            if phase in ('qa','review','human_review','done'):
                c.store.evidence(job['id'], sha, 'qa', {'passed': True})
            if phase in ('human_review','done'):
                c.store.evidence(job['id'], sha, 'review', {'approved': True, 'provider': 'antigravity'})
    for day in range(7):
        for run in range(4+(day*3)%9):
            job = jobs[(day+run)%len(jobs)]
            provider = 'antigravity' if run%3 == 0 else 'codex'
            stamp = (now-timedelta(days=6-day, hours=run+1)).isoformat()
            data = {'input_tokens': 2100+day*713+run*179, 'output_tokens': 460+run*121}
            with c.store.db:
                c.store.db.execute('INSERT INTO usage(job,provider,model,data,seconds,created,phase,outcome) VALUES (?,?,?,?,?,?,?,?)',
                    (job,provider,'demo-model',json.dumps(data),25+run*17,stamp,'review' if provider=='antigravity' else 'coding','succeeded'))
    c.store.usage(jobs[0], 'codex', 'demo-model', {'input_tokens': 0, 'output_tokens': 0}, 1)
    c.store.db.close()
    return config

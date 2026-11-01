"""Bounded same-route verdict clarification with durable request lineage."""
import json
import uuid


def execute(store, job, sha, provider, model, operation):
    parent = None
    for _ in range(2):
        with store.write(job):
            rows = store.db.execute("SELECT data FROM evidence WHERE job=? AND sha=? AND kind='review_verdict_attempt' ORDER BY id", (job, sha)).fetchall()
            records = [json.loads(row[0]) for row in rows]
            claims = [r for r in records if r['status'] == 'claimed']
            finished = {r['token'] for r in records if r['status'] != 'claimed'}
            if any(r['token'] not in finished for r in claims):
                raise RuntimeError('Interrupted review requires reconciliation')
            same = [r for r in claims if r['provider'] == provider]
            invalid = [r for r in records if r['status'] == 'invalid' and r['provider'] == provider]
            if len(invalid) >= 2:
                raise RuntimeError('Verdict clarification budget exhausted')
            if same and same[-1]['model'] != model:
                raise RuntimeError('Verdict clarification cannot change reviewer model')
            parent = claims[-1]['token'] if claims else None
            record = dict(token=uuid.uuid4().hex, parent=parent, provider=provider,
                          model=model, status='claimed', purpose='clarification' if same else 'primary')
            store.evidence(job, sha, 'review_verdict_attempt', record)
        usage_start = store.db.execute('SELECT COALESCE(MAX(id),0) FROM usage WHERE job=?', (job,)).fetchone()[0]
        def finish(status):
            usage_ids = [r[0] for r in store.db.execute('SELECT id FROM usage WHERE job=? AND id>? ORDER BY id', (job, usage_start))]
            store.evidence(job, sha, 'review_verdict_attempt', dict(record, status=status, usage_ids=usage_ids))
        try:
            result = operation(store.review_context(job, sha), bool(same))
        except ValueError:
            finish('invalid')
            continue
        except Exception:
            finish('failed')
            raise
        finish('complete')
        return result
    raise RuntimeError('Verdict clarification budget exhausted')

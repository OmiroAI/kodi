"""Cross-process Qwen admission in the existing append-only evidence ledger.

An interrupted reservation is never expired: remote completion must be reconciled
before an operator can release it. No stage slot is held while waiting.
"""
import json
import time
import uuid
from contextlib import contextmanager


class AdmissionBusy(Exception):
    pass


@contextmanager
def request(store, job, sha, provider):
    if provider != 'qwen':
        yield
        return
    token = uuid.uuid4().hex
    queued = time.time()
    busy = False
    with store.write(job):
        row = store.db.execute("SELECT data FROM evidence WHERE job=0 AND sha='qwen' AND kind='review_request' ORDER BY id DESC LIMIT 1").fetchone()
        previous = json.loads(row[0]) if row else None
        wait = store.db.execute("SELECT data FROM evidence WHERE job=? AND sha=? AND kind='review_admission_wait' ORDER BY id DESC LIMIT 1", (job, sha)).fetchone()
        wait = json.loads(wait[0]) if wait else None
        if wait and wait['status'] == 'waiting':
            queued = wait['started']
        if previous and previous['status'] == 'running':
            if not wait or wait['status'] != 'waiting':
                store.evidence(job, sha, 'review_admission_wait', dict(status='waiting', started=queued))
            busy = True
        else:
            record = dict(token=token, job=job, candidate=sha, status='running',
                          started=time.time(), previous=previous['token'] if previous else None)
            store.evidence(0, 'qwen', 'review_request', record)
            store.evidence(job, sha, 'review_admission_wait', dict(status='admitted', started=queued,
                queue_seconds=max(0, record['started']-queued), token=token))
    if busy:
        raise AdmissionBusy('Qwen request requires completion or reconciliation')
    completed = False
    try:
        yield record
        completed = True
    except Exception:
        # Synchronous transport failures have returned; process interruption and
        # stale execution (BaseException) retain the uncertain reservation.
        completed = True
        raise
    finally:
        if completed:
            with store.write(job):
                store.evidence(0, 'qwen', 'review_request', {**record, 'status':'complete',
                    'admission_seconds':max(0, time.time()-record['started']),
                    'request_seconds':record.get('request_seconds', 0),
                    'queue_seconds':max(0, record['started']-queued)})

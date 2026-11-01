"""Durable review reservations in the existing evidence ledger (no schema changes)."""
import json
import math
import time
import uuid
from datetime import timezone
from email.utils import parsedate_to_datetime

from .failures import classify, fingerprint


class ReviewAuthorizationChanged(RuntimeError):
    """Retained inputs or owner scheduling changed; never move the owner task."""


class ReviewWaiting(Exception):
    pass


def latest(store, job):
    row = store.db.execute("SELECT data FROM evidence WHERE job=? AND kind='delayed_review' ORDER BY id DESC LIMIT 1", (job,)).fetchone()
    return json.loads(row[0]) if row else None


def policy(config):
    value = dict(timeout=config.get('worker_timeout', 900), http_timeout=180, attempts=3,
                 backoff=[30, 120], window=900)
    if type(value['timeout']) is not int or not 1 <= value['timeout'] <= 86400:
        raise ValueError('Invalid review timeout')
    return value


def deadline(value, now):
    if value is None:
        return now
    if isinstance(value, str):
        if value.isdigit():
            value = int(value)
        else:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                raise ValueError('Retry date requires timezone')
            return max(now, parsed.astimezone(timezone.utc).timestamp())
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid provider deadline')
    return now + value


def execute(store, job, sha, config, identity, operation, validate, reconcile, clock=None):
    clock = clock or time.time
    job_id = job['id']
    provider_key = fingerprint({key: identity.get(key) for key in ('provider', 'model', 'fallback_model')})
    def save(record):
        record['remaining_attempts'] = record['policy']['attempts'] - record['consumed']
        store.evidence(job_id, sha, 'delayed_review', record)
    refusal = None
    waiting = False
    with store.write(job_id):
        record = latest(store, job_id)
        binding = fingerprint([job_id, sha, job['requirements'], job['pr_url'], job['branch'],
                               job['repo'], job['board_id'], job['external_id'], identity])
        if record and record['status'] == 'complete' and record['binding'] != binding:
            record = None  # New successfully validated candidate, not failure recovery.
        if record:
            if record['binding'] != binding:
                raise RuntimeError('Review inputs changed; retained budget requires inspection')
            if record['status'] in ('claimed', 'blocked', 'complete'):
                raise RuntimeError('Review already consumed or uncertain; reconcile retained request')
            if clock() < record['next_retry_at']:
                raise ReviewWaiting()
        else:
            effective = policy(config)
            record = dict(binding=binding, policy=effective, consumed=0,
                          expires_at=clock()+effective['window'])
        validate()
        if clock() > record['expires_at'] or record['consumed'] >= record['policy']['attempts']:
            record.update(status='blocked', category='retry_exhausted')
            save(record)
            refusal = 'Review retry window or budget exhausted'
        else:
            if record['consumed']:
                reconcile()  # Remote state can drift during backoff.
            admission = store.db.execute("SELECT data FROM evidence WHERE job=0 AND sha=? AND kind='review_provider_admission' ORDER BY id DESC LIMIT 1", (provider_key,)).fetchone()
            due = json.loads(admission[0])['next_retry_at'] if admission else clock()
            if due > clock():
                if due > record['expires_at']:
                    record.update(status='blocked', category='provider_deadline_outside_window')
                    refusal = 'Provider deadline exceeds retry window'
                else:
                    reconcile()
                    record.update(status='waiting', next_retry_at=due, reconciled=True, category='provider_admission')
                    store.update(job_id, state='review_wait', error=None)
                    waiting = True
            else:
                record.update(status='claimed', claim=uuid.uuid4().hex,
                              request_identity=fingerprint([binding, record['consumed']]),
                              consumed=record['consumed']+1)
                store.update(job_id, state='review', error=None)
            save(record)
            claim = record.get('claim')
    if refusal:
        raise RuntimeError(refusal)
    if waiting:
        raise ReviewWaiting()
    try:
        result = operation(record['policy'])
    except Exception as error:
        from .reviewer_admission import AdmissionBusy
        if isinstance(error, AdmissionBusy):
            with store.write(job_id):
                record.update(status='waiting', consumed=record['consumed']-1,
                              next_retry_at=clock()+1, category='provider_admission')
                save(record)
                store.update(job_id, state='review_wait', error=None)
            raise ReviewWaiting() from None
        diagnostic = classify(error)
        with store.write(job_id):
            current = latest(store, job_id)
            if current['claim'] != claim:
                raise RuntimeError('Stale review result') from None
            record.update(status='blocked', category=diagnostic['category'])
            if diagnostic['retry_eligible'] and record['consumed'] < record['policy']['attempts']:
                try:
                    due = max(clock()+record['policy']['backoff'][record['consumed']-1],
                              deadline(getattr(error, 'retry_after', None), clock()))
                    reset_at = getattr(error, 'reset_at', None)
                    if reset_at is not None:
                        if type(reset_at) not in (int, float) or not math.isfinite(reset_at) or reset_at < 0:
                            raise ValueError('Invalid documented reset')
                        due = max(due, reset_at)
                    if due <= record['expires_at']:
                        validate()
                        reconcile()
                        record.update(status='waiting', next_retry_at=due, reconciled=True)
                        store.evidence(0, provider_key, 'review_provider_admission', {'next_retry_at':due})
                    else:
                        record['category'] = 'provider_deadline_outside_window'
                except Exception:
                    pass  # Unknown deadlines/remote state must never retry early.
            save(record)
            if record['status'] == 'waiting':
                store.update(job_id, state='review_wait', error=None)
        if record['status'] == 'waiting':
            raise ReviewWaiting() from None
        raise
    with store.write(job_id):
        validate()
        if latest(store, job_id)['claim'] != claim:
            raise RuntimeError('Stale review result')
        record.update(status='complete')
        save(record)
    return result

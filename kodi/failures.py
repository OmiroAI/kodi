"""Allowlisted delivery diagnostics. Raw adapter output belongs in private artifacts."""
import hashlib
import json
import math
import subprocess
import time

POLICIES = {
    'code_test': ('Repair candidate within the existing budget', 'coding worker', False),
    'resource': ('Confirm resource or candidate change', 'operator', False),
    'timeout': ('Inspect deadline and confirm relevant change', 'operator', False),
    'ci_unavailable': ('Restore required CI; retain required-check policy', 'operator', False),
    'provider_unavailable': ('Wait for provider recovery', 'operator', True),
    'throttle': ('Wait for bounded backoff', 'coordinator', True),
    'quota': ('Confirm quota recovery', 'operator', False),
    'configuration': ('Correct access or configuration', 'operator', False),
    'unknown': ('Inspect private evidence and record relevant recovery', 'operator', False),
}
SIGNALS = {'test_diagnostic': 'code_test', 'blocking_review': 'code_test',
           'oom_event': 'resource', 'execution_deadline': 'timeout',
           'transport_timeout': 'timeout', 'ci_disabled': 'ci_unavailable',
           'ci_outage': 'ci_unavailable', 'provider_outage': 'provider_unavailable',
           'http_429': 'throttle', 'explicit_quota': 'quota',
           'http_401': 'configuration', 'http_403': 'configuration',
           'invalid_configuration': 'configuration'}


class DeliveryFailure(RuntimeError):
    def __init__(self, signal='unknown', retry_after=None):
        self.signal = signal if signal in SIGNALS else 'unknown'
        self.retry_after = retry_after
        super().__init__(summary(classify(self)))


def classify(error):
    signal = getattr(error, 'signal', 'unknown')
    if isinstance(error, subprocess.TimeoutExpired):
        signal = 'execution_deadline'
    category = SIGNALS.get(signal, 'unknown')
    action, actor, automatic = POLICIES[category]
    if signal in ('transport_timeout', 'ci_outage'):
        automatic = True
    return dict(category=category, reason=signal if signal in SIGNALS else 'unknown',
                next_action=action, responsible_actor=actor, retry_eligible=automatic)


def summary(record):
    return record['category'] + ': ' + record['next_action']


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def latest(store, job, stage, candidate):
    row = store.db.execute("SELECT data FROM evidence WHERE job=? AND sha=? AND kind IN (?,?) ORDER BY id DESC LIMIT 1",
                           (job, candidate, 'failure:'+stage, 'delivery_attempt:'+stage)).fetchone()
    return json.loads(row[0]) if row else None


def record(store, job, stage, candidate, error, **metadata):
    value = classify(error)
    value.update(stage=stage, candidate=candidate, request_identity=fingerprint([job, stage, candidate]),
                 evidence_reference='private-attempt:'+fingerprint([job, stage, candidate])[:24])
    value.update(metadata)
    store.evidence(job, candidate, ('delivery_attempt:' if metadata.get('reservation') or metadata.get('completed') else 'failure:')+stage, value)
    return value


def execute(store, job, stage, candidate, operation, recovery_inputs=None):
    """Persist reservations and deadlines before work; unchanged failures cannot replay."""
    generation = fingerprint(recovery_inputs)
    previous = latest(store, job, stage, candidate)
    if previous and not previous.get('completed') and previous.get('generation') == generation:
        raise RuntimeError('Automatic recovery budget exhausted; relevant recovery required')  # Explicit recovery inputs, never a tick, unlock a failed invocation.
    count = 0
    started = time.time()
    while True:
        # An interrupted invocation is uncertain and requires inspection after restart.
        record(store, job, stage, candidate, DeliveryFailure(), generation=generation,
               retry_count=count, reservation=True)
        try:
            result = operation()
            record(store, job, stage, candidate, DeliveryFailure(), generation=generation, completed=True)
            return result
        except Exception as error:
            value = record(store, job, stage, candidate, error, generation=generation,
                           retry_count=count, reservation=False)
            if count >= 2 and value['retry_eligible']:
                raise RuntimeError('Automatic recovery budget exhausted') from None
            if not value['retry_eligible']:
                raise
            delay = (30, 120)[count]
            supplied = getattr(error, 'retry_after', None)
            if supplied is not None:
                if type(supplied) not in (int, float) or not math.isfinite(supplied) or supplied < 0 or supplied > 900:
                    raise
                delay = max(delay, supplied)
            deadline = time.time() + delay
            if deadline > started + 900:
                raise
            record(store, job, stage, candidate, error, generation=generation,
                   retry_count=count+1, next_eligible_time=deadline)
            time.sleep(max(0, deadline-time.time()))
            count += 1


def provider_failure(message):
    """Inspect locally; never retain the response in a diagnostic."""
    from .runner import quota_exhausted
    text = message.lower()
    if quota_exhausted(text):
        return DeliveryFailure('explicit_quota')
    for marker, signal in (('401', 'http_401'), ('403', 'http_403'),
                           ('429', 'http_429'), ('503', 'provider_outage'),
                           ('service unavailable', 'provider_outage')):
        if marker in text:
            return DeliveryFailure(signal)
    if any(marker in text for marker in ('connection reset', 'connection refused',
                                         'temporary failure', 'connection closed unexpectedly')):
        return DeliveryFailure('provider_outage')
    if 'request failed:' in text and ': eof' in text:
        return DeliveryFailure('provider_outage')
    return DeliveryFailure()

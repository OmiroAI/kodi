"""Bounded local observations, independent of acceptance and accounting delivery.

One owner can have one open interval. Different owners may overlap; wall time is
partitioned with execution > blocked > wait precedence. Monotonic durations are
valid only in the observing process. Restart gaps are explicitly unknown.
"""
import hashlib
import json
import time
import uuid

LIMIT = 128
STATES = {
    'retry_backoff': ('waiting', 'provider', 'agent', 'retry'),
    'queued': ('waiting', 'stage', 'agent', 'dispatch'),
    'coding': ('execution', 'coding', 'agent', 'run'),
    'qa': ('execution', 'qa', 'agent', 'run'),
    'review': ('execution', 'review', 'agent', 'run'),
    'publishing': ('execution', 'delivery', 'agent', 'run'),
    'merge_updating': ('execution', 'recovery', 'agent', 'run'),
    'validated': ('waiting', 'delivery', 'operator', 'inspect'),
    'ci_pending': ('waiting', 'checks', 'external_service', 'complete_checks'),
    'human_review': ('waiting', 'approval', 'reviewer', 'approve'),
    'merge_pending': ('waiting', 'delivery', 'external_service', 'complete_merge'),
    'review_wait': ('waiting', 'review', 'coordinator', 'wait for scheduled review retry'),
    'blocked': ('blocked', 'intervention', 'operator', 'inspect'),
    'done': ('terminal', 'done', None, None),
    'cancelled': ('terminal', 'cancelled', None, None),
}


class Timing:
    def __init__(self, db, clock=time.monotonic, wall=time.time):
        self.db, self.clock, self.wall = db, clock, wall
        self.session = str(uuid.uuid4())
        db.execute('''CREATE TABLE IF NOT EXISTS phase_history (
            job INTEGER PRIMARY KEY, data TEXT NOT NULL)''')

    def read(self, job):
        row = self.db.execute('SELECT data FROM phase_history WHERE job=?', (job,)).fetchone()
        return json.loads(row[0]) if row else {'events': [], 'totals': {}, 'truncated': False,
                                               'coverage_start': None, 'unknown': True}

    def observe(self, job, state, attempt, sha, owner='coordinator', complete=False):
        if owner not in ('coordinator', 'coding', 'qa', 'review', 'approval', 'intervention', 'recovery'):
            raise ValueError('Unsupported interval owner')
        if type(attempt) is not int or not 0 <= attempt < 2**31:
            raise ValueError('Invalid phase attempt')
        if sha is not None and (not isinstance(sha, str) or len(sha) > 64 or any(c not in '0123456789abcdef' for c in sha)):
            sha = None
        category, phase, actor, action = STATES.get(state, STATES['blocked'])
        data = self.read(job)
        if complete and data['coverage_start'] is None:
            data['unknown'] = False
        events = data['events']
        job_row = self.db.execute('SELECT requirements FROM jobs WHERE id=?', (job,)).fetchone()
        context = hashlib.sha256(job_row[0].encode()).hexdigest() if job_row else None
        identity = [state if state in STATES else 'blocked', attempt, sha, context]
        active = next((e for e in reversed(events) if e['owner'] == owner and e['end'] is None), None)
        tick, stamp = self.clock(), self.wall()
        previous = next((e for e in reversed(events) if e['owner'] == owner), None)
        if previous and previous['identity'] == identity and previous['session'] == self.session:
            return
        if active:
            active['end'] = stamp
            active['duration'] = (max(0, tick-active['tick'])
                                  if active['session'] == self.session and tick >= active['tick'] else None)
            active['outcome'] = 'observed_end' if active['duration'] is not None else 'interrupted'
            if active['duration'] is None:
                data['unknown'] = True
        # Ended terminal observations do not accrue time.
        data['sequence'] = data.get('sequence', 0) + 1
        events.append(dict(event_id=str(job)+':'+str(data['sequence']), identity=identity, owner=owner, category=category, phase=phase,
                           actor=actor, action=action, reason=phase, attempt=attempt,
                           candidate_sha=sha, start=stamp, end=stamp if category == 'terminal' else None,
                           duration=0 if category == 'terminal' else None,
                           outcome='terminal' if category == 'terminal' else 'open',
                           tick=tick, session=self.session))
        if data['coverage_start'] is None:
            data['coverage_start'] = stamp
        # Checkpoint the union, splitting open spans at the checkpoint. Earlier
        # detail is discarded only after its disjoint totals have been retained.
        if len(events) > LIMIT:
            snapshot = json.loads(json.dumps(events))
            for e in snapshot:
                if e['end'] is None and e['session'] == self.session and tick >= e['tick']:
                    e['duration'] = tick-e['tick']
            for key, value in self.partition(snapshot).items():
                data['totals'][key] = data['totals'].get(key, 0) + value
            data['events'] = [e for e in events if e['end'] is None]
            for e in data['events']:
                if e['session'] != self.session:
                    data['unknown'] = True
                    continue
                e['tick'] = tick
                e['session'] = self.session
                e['duration'] = None
            data['truncated'] = True
        self.db.execute('INSERT OR REPLACE INTO phase_history VALUES (?,?)', (job, json.dumps(data)))

    @staticmethod
    def partition(events):
        spans = []
        for e in events:
            if e['duration'] is not None and e['category'] != 'terminal':
                # Use monotonic coordinates within a session, never wall-clock deltas.
                spans.append((e['session'], e['tick'], e['tick']+e['duration'], e['category']))
        result = dict(execution=0, blocked=0, waiting=0)
        for session in {s[0] for s in spans}:
            rows = [s for s in spans if s[0] == session]
            points = sorted({p for s in rows for p in s[1:3]})
            for a, b in zip(points, points[1:]):
                categories = {s[3] for s in rows if s[1] <= a and s[2] >= b}
                for category in ('execution', 'blocked', 'waiting'):
                    if category in categories:
                        result[category] += b-a
                        break
        return result

    def project(self, job):
        data = self.read(job)
        tick = self.clock()
        for e in data['events']:
            if e['end'] is None and e['session'] == self.session and tick >= e['tick']:
                e['duration'] = tick-e['tick']
        totals = self.partition(data['events'])
        for key, value in data['totals'].items():
            totals[key] += value
        current = [e for e in data['events'] if e['end'] is None]
        interrupted = any(e['session'] != self.session or tick < e['tick'] for e in current)
        if interrupted:
            data['unknown'] = True
        ordered = sorted(current, key=lambda e: ({'execution':0, 'blocked':1, 'waiting':2}[e['category']], e['owner']))
        return dict(as_of=self.wall(), coverage_start=data['coverage_start'],
                    history_partial=data['unknown'], truncated=data['truncated'], seconds=totals,
                    current_blockers=([e['reason'] for e in current if e['category'] != 'execution']
                                      + (['interrupted'] if interrupted else [])),
                    next_action='inspect' if interrupted else ordered[0]['action'] if ordered else None,
                    actor='operator' if interrupted else ordered[0]['actor'] if ordered else None,
                    history=data['events'],
                    worker_seconds=None)

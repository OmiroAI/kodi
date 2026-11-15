"""Small authenticated operator dashboard over Kodi's existing task and job ledger."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit, parse_qs

from .coordinator import Coordinator
from .delivery_lock import acquire, status as lock_status
from .job_control import inspect as inspect_job, control
from .native_board import STAGES
from .readiness import blockers
from .usage import usage_summary

ASSETS = Path(__file__).with_name('dashboard_static')
STATE_STAGE = {'queued': 'Ready', 'intake_pending': 'Ready', 'coding': 'Coding',
               'qa': 'QA', 'review': 'Review', 'review_wait': 'Review', 'validated': 'Review',
               'publishing': 'Review', 'ci_pending': 'Review', 'ci_failed': 'Blocked',
               'human_review': 'Human review', 'merge_pending': 'Human review',
               'merge_updating': 'Human review', 'blocked': 'Blocked', 'needs_input': 'Needs input',
               'paused': 'Blocked', 'done': 'Done', 'cancelled': 'Done'}


@contextmanager
def coordinator(config):
    c = Coordinator(dict(config))
    try:
        yield c
    finally:
        c.store.db.close()


def stamp(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Activity timestamps must include a timezone')
    return value.astimezone(timezone.utc)


def statistics(store, *, days=7, repo=None, provider=None, offset=0, limit=30, now=None):
    """Filter event membership before paging; zero and unknown usage remain activity."""
    if days not in (0, 7, 30) or type(offset) is not int or offset < 0 or not 1 <= limit <= 100:
        raise ValueError('Invalid statistics range or page')
    end = now or datetime.now(timezone.utc)
    start = end-timedelta(days=days) if days else None
    jobs = {r['id']: dict(r) for r in store.db.execute('SELECT * FROM jobs')}
    active, rows, daily = {}, [], {}
    def eligible(job, created, event_provider):
        moment = stamp(created)
        return (job in jobs and (not repo or jobs[job]['repo'] == repo)
                and (not provider or event_provider == provider)
                and (start is None or moment >= start) and moment < end)
    def activity(job, created):
        moment = stamp(created)
        active[job] = max(active.get(job, moment), moment)
    for row in store.db.execute('SELECT * FROM usage ORDER BY id'):
        if eligible(row['job'], row['created'], row['provider']):
            rows.append(row)
            activity(row['job'], row['created'])
            daily.setdefault(stamp(row['created']).date().isoformat(), []).append(row)
    counts = {}
    for row in store.db.execute("SELECT job,kind,data,created FROM evidence WHERE kind IN ('qa','review')"):
        data = json.loads(row['data'])
        if data.get('invalidation'):
            continue
        event_provider = 'qa' if row['kind'] == 'qa' else data.get('provider')
        if eligible(row['job'], row['created'], event_provider):
            activity(row['job'], row['created'])
            item = counts.setdefault(row['job'], {'qa_runs': 0, 'review_iterations': 0})
            item['qa_runs' if row['kind'] == 'qa' else 'review_iterations'] += 1
    ids = sorted(active, key=lambda key: (active[key], key), reverse=True)
    task_rows = []
    for key in ids[offset:offset+limit]:
        job = jobs[key]
        task_rows.append({**{k: job[k] for k in ('id', 'title', 'repo', 'state')},
                          'last_activity': active[key].isoformat(), 'providers': usage_summary([r for r in rows if r['job'] == key]),
                          **counts.get(key, {'qa_runs': 0, 'review_iterations': 0})})
    known_tokens, unknown_runs = 0, 0
    for row in rows:
        data = json.loads(row['data'])
        keys = ('prompt_tokens', 'completion_tokens') if row['provider'] == 'qwen' else ('input_tokens', 'output_tokens')
        values = [data.get(key) if isinstance(data, dict) else None for key in keys]
        known_tokens += sum(value for value in values if type(value) is int and value >= 0)
        unknown_runs += int(any(type(value) is not int or value < 0 for value in values))
    return {'known_tokens': known_tokens, 'unknown_runs': unknown_runs,
            'providers': usage_summary(rows), 'daily': [{'date': day, 'providers': usage_summary(items)}
            for day, items in sorted(daily.items())], 'tasks': task_rows, 'task_count': len(ids),
            'next_offset': offset+limit if offset+limit < len(ids) else None,
            'runs': len(rows), 'qa_runs': sum(c['qa_runs'] for c in counts.values()),
            'review_iterations': sum(c['review_iterations'] for c in counts.values()),
            'start': start.isoformat() if start else None, 'end': end.isoformat()}


class Dashboard:
    def __init__(self, config, *, demo=False):
        if config.get('board_backend') != 'native':
            raise ValueError('Enable the native board only after importing existing tasks')
        self.config, self.demo = config, demo
        # Initialize once, never by an unauthenticated HTTP request.
        with coordinator(config):
            pass

    def state(self):
        with coordinator(self.config) as c:
            settings = c.config['board']
            names = {v: k for k, v in settings['steps'].items()}
            jobs = [dict(r) for r in c.store.db.execute('SELECT * FROM jobs ORDER BY id DESC')]
            by_task = {}
            for job in jobs:
                by_task.setdefault(job['board_id'], job)
            tasks = []
            for task in c.board.tasks(settings):
                job = by_task.get(task['id'])
                description = task.get('description', '').split('\n\n---\nKodi status:')[0]
                key = c.task_repository(task, description, settings)
                owner = c.store.repository_blocker(job) if job and job['state'] == 'queued' else None
                reasons = c.dependency_blockers(job) if job else blockers(key, self.config['repos'].get(key, {'enabled': False}), description)
                if job:
                    from .learning import waiting_reason
                    learning_wait = waiting_reason(self.config, job)
                    if learning_wait:
                        reasons.append(learning_wait)
                    capacity_wait = c.store.db.execute('SELECT reason FROM capacity_waits WHERE job=?', (job['id'],)).fetchone()
                    if capacity_wait:
                        reasons.append(capacity_wait['reason'])
                    if owner:
                        reasons.append('Waiting for task #'+str(owner['id'])+' in this repository')
                    if job['error']:
                        reasons.append(job['error'])
                tasks.append({'id': task['id'], 'title': task['title'], 'description': description,
                              'repo': job['repo'] if job else key,
                              'stage': STATE_STAGE.get(job['state'], 'Blocked') if job else names.get(task['workflow_step_id'], 'Backlog'),
                              'job': ({k: job[k] for k in ('id', 'state', 'attempt', 'commit_sha', 'pr_url', 'error', 'updated')} if job else None),
                              'wait_reason': '; '.join(dict.fromkeys(reasons)) or None,
                              'editable': not job or (job['state'] == 'queued' and job['attempt'] == 0 and not job['commit_sha'] and not job['pr_url'])})
            return {'demo': self.demo, 'stages': STAGES, 'tasks': tasks,
                    'repositories': [{'key': key, 'enabled': r.get('enabled', True),
                                      'auto_merge': r.get('auto_merge', False)} for key, r in self.config['repos'].items()],
                    'capacity': self.config.get('capacity'),
                    'occupied': [dict(r) for r in c.store.db.execute('SELECT job,stage,provider FROM capacity_claims WHERE released IS NULL')],
                    'invocations': [dict(r) for r in c.store.db.execute("SELECT job,stage FROM invocations WHERE status='running'")],
                    'delivery_lock': lock_status(self.config['state_dir'])}

    def detail(self, job_id):
        with coordinator(self.config) as c:
            job = c.store.get(job_id)
            if not job:
                raise ValueError('Job not found')
            plan = inspect_job(c.store, job_id)
            events = []
            for row in c.store.db.execute('SELECT id,kind,sha,data,created FROM evidence WHERE job=? ORDER BY id DESC LIMIT 60', (job_id,)):
                data = json.loads(row['data'])
                events.append({'id': row['id'], 'kind': row['kind'], 'sha': row['sha'], 'created': row['created'],
                               'result': {k: data[k] for k in ('approved', 'passed', 'reason', 'next_action', 'responsible_actor') if k in data}})
            saved = plan['checkpoint'] or {}
            can_resume = (job['state'] == 'paused' and saved.get('state') in
                          ('queued','qa','review','validated','review_wait','blocked')
                          and not plan['capacity'] and not any(r['status'] == 'running' for r in plan['invocations']))
            pause_ids = c.store.db.execute("SELECT kind,MAX(id) AS id FROM evidence WHERE job=? AND kind IN ('pause_request','pause_resumed') GROUP BY kind", (job_id,)).fetchall()
            pause_ids = {r['kind']: r['id'] for r in pause_ids}
            pause_pending = job['state'] != 'paused' and pause_ids.get('pause_request',0) > pause_ids.get('pause_resumed',0)
            return {'control': {'digest': plan['digest'], 'state': job['state'],
                                'can_pause': not pause_pending and job['state'] not in ('done','cancelled','paused','publishing','merge_pending','merge_updating'),
                                'can_resume': can_resume, 'can_prioritize': job['state'] == 'queued',
                                'pause_pending': pause_pending,
                                'resume_blocker': ('Inspect retained coding or uncertain execution before resuming.' if job['state']=='paused' and not can_resume else None)},
                    'events': events, 'timing': c.store.delivery_timing(job_id),
                    'usage': usage_summary(c.store.db.execute('SELECT * FROM usage WHERE job=?', (job_id,)))}

    @staticmethod
    def requirements(config, repo, description):
        if repo not in config['repos'] or not isinstance(description, str) or not 1 <= len(description.strip()) <= 12000:
            raise ValueError('Select a repository and supply a description of at most 12000 characters')
        routes = re.findall(r'^Repo:\s*(\S+)\s*$', description, re.M)
        if routes and (len(routes) != 1 or routes[0] != repo):
            raise ValueError('Description repository conflicts with selection')
        return description.strip() if routes else 'Repo: '+repo+'\n\n'+description.strip()

    @staticmethod
    def validate_editor(value):
        if (set(value) != {'title', 'repo', 'description'} or not isinstance(value['title'], str)
                or not 1 <= len(value['title'].strip()) <= 60):
            raise ValueError('Supply a title of 1–60 characters, a repository and a description')

    def mutate(self, path, value):
        with coordinator(self.config) as c, acquire(self.config['state_dir'], 'dashboard operator action'):
            settings = c.config['board']
            if path == '/api/tasks':
                self.validate_editor(value)
                text = self.requirements(self.config, value['repo'], value['description'])
                return c.board.create(settings, value['title'], text, None,
                                      settings['repository_ids'].get(value['repo']))
            match = re.fullmatch(r'/api/tasks/([a-zA-Z0-9_-]+)(/stage)?', path)
            if match:
                task_id, moving = match.groups()
                task = c.board.request('/tasks/'+task_id)
                job = c.store.db.execute('SELECT * FROM jobs WHERE board_id=? ORDER BY id DESC LIMIT 1', (task_id,)).fetchone()
                if moving:
                    if set(value) != {'stage'} or value['stage'] not in ('Backlog', 'Ready') or job:
                        raise ValueError('Only unstarted drafts can move; use job controls after dispatch')
                    if value['stage'] == 'Ready':
                        repo = c.task_repository(task, task['description'], settings)
                        reasons = blockers(repo, self.config['repos'].get(repo, {'enabled': False}), task['description'])
                        if reasons:
                            raise ValueError('; '.join(reasons))
                    return c.board.move(settings, task_id, value['stage'])
                self.validate_editor(value)
                text = self.requirements(self.config, value['repo'], value['description'])
                c.board.validate(value['title'], text)
                if job:
                    if job['state'] != 'queued' or job['attempt'] or job['commit_sha'] or job['pr_url'] or job['repo'] != value['repo']:
                        raise ValueError('Execution evidence exists; change scope in a follow-up ticket')
                    if c.store.db.execute("SELECT 1 FROM invocations WHERE job=? AND status='running'", (job['id'],)).fetchone():
                        raise ValueError('An executor owns this task')
                    with c.store.write(job['id']):
                        row = c.store.db.execute("SELECT id,data FROM evidence WHERE job=? AND kind='delivery_contract' ORDER BY id LIMIT 1", (job['id'],)).fetchone()
                        old = json.loads(row['data']) if row else None
                        c.store.evidence(job['id'], '', 'owner_scope_revision', {'previous_requirements': job['requirements'],
                                         'previous_contract': old, 'requirements': text, 'owner_reference': 'Authenticated dashboard operator edit before execution'})
                        if row:
                            old.update(requirements=text, reason='Authenticated owner edit before execution')
                            c.store.db.execute('UPDATE evidence SET data=? WHERE id=?', (json.dumps(old), row['id']))
                        c.store.update(job['id'], title=value['title'], requirements=text)
                        c.store.db.execute("UPDATE clarifications SET state='superseded' WHERE job=? AND state='actionable'", (job['id'],))
                return c.board.request('/tasks/'+task_id, {'title': value['title'], 'description': text}, method='PATCH')
            match = re.fullmatch(r'/api/jobs/(\d+)/control', path)
            if match:
                if set(value) != {'action', 'digest'} or value['action'] not in ('pause', 'resume', 'prioritize'):
                    raise ValueError('Unsupported control action')
                control(c.store, int(match[1]), value['action'], value['digest'], 'Authenticated dashboard operator')
                return {'ok': True}
            raise ValueError('Unknown operation')


def handler(app, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Authorization headers and task content never enter HTTP access logs.

        def reply(self, code, value, content_type='application/json'):
            raw = json.dumps(value, allow_nan=False).encode() if content_type == 'application/json' else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            supplied = self.headers.get('X-Kodi-Token')
            if supplied is None:
                authorization = self.headers.get('Authorization', '')
                supplied = authorization[7:] if authorization.startswith('Bearer ') else ''
            return app.demo or (bool(token) and hmac.compare_digest(supplied.encode(), token.encode()))

        def do_GET(self):
            parsed = urlsplit(self.path)
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if parsed.path in assets:
                name, kind = assets[parsed.path]
                return self.reply(200, (ASSETS/name).read_bytes(), kind)
            if not self.authorized():
                return self.reply(401, {'error': 'Operator token required'})
            try:
                if parsed.path == '/api/state':
                    return self.reply(200, app.state())
                match = re.fullmatch(r'/api/jobs/(\d+)', parsed.path)
                if match:
                    return self.reply(200, app.detail(int(match[1])))
                if parsed.path == '/api/stats':
                    q = parse_qs(parsed.query)
                    with coordinator(app.config) as c:
                        return self.reply(200, statistics(c.store, days=int(q.get('days', ['7'])[0]),
                            repo=q.get('repo', [None])[0], provider=q.get('provider', [None])[0], offset=int(q.get('offset', ['0'])[0])))
                self.reply(404, {'error': 'Not found'})
            except (ValueError, KeyError, TypeError):
                self.reply(400, {'error': 'Invalid request'})
            except Exception:
                self.reply(503, {'error': 'Dashboard data is temporarily unavailable'})

        def do_POST(self):
            if not self.authorized():
                return self.reply(401, {'error': 'Operator token required'})
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                return self.reply(403, {'error': 'Cross-origin actions are not permitted'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536 or self.headers.get_content_type() != 'application/json':
                    raise ValueError('Invalid JSON body')
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError('Invalid JSON object')
                result = app.mutate(urlsplit(self.path).path, value)
                self.reply(200, result)
            except (ValueError, KeyError, TypeError) as error:
                self.reply(409, {'error': str(error)[:500]})
            except RuntimeError:
                self.reply(409, {'error': 'Delivery is busy or the checkpoint changed; refresh and inspect again'})
            except Exception:
                self.reply(503, {'error': 'Action failed; inspect the retained job before retrying'})
    return Handler


def create_server(config, host='127.0.0.1', port=8810, *, demo=False):
    token = os.environ.get('KODI_DASHBOARD_TOKEN', '')
    if not demo and len(token) < 32:
        raise ValueError('Set KODI_DASHBOARD_TOKEN to at least 32 characters')
    if demo and not config.get('_dashboard_demo'):
        raise ValueError('Demo mode requires an isolated fictional ledger')
    app = Dashboard(config, demo=demo)
    server = ThreadingHTTPServer((host, port), handler(app, token))
    server.daemon_threads = True
    return server


def start_embedded(config):
    settings = config.get('dashboard', {})
    if not isinstance(settings, dict) or type(settings.get('enabled', False)) is not bool:
        raise ValueError('dashboard.enabled must be a boolean')
    if not settings.get('enabled'):
        return None
    port = settings.get('port', 8810)
    host = settings.get('host', '127.0.0.1')
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(host, str) or not host:
        raise ValueError('Invalid dashboard bind address or port')
    server = create_server(config, host, port)
    threading.Thread(target=server.serve_forever, name='kodi-dashboard', daemon=True).start()
    print('Native Kodi dashboard started', flush=True)
    return server


def serve(config, host='127.0.0.1', port=8810, *, demo=False):
    server = create_server(config, host, port, demo=demo)
    print('Kodi dashboard listening on '+host+':'+str(server.server_port), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()

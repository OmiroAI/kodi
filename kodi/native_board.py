"""Kodi-owned task storage implementing the coordinator's existing board contract."""
from contextlib import contextmanager
import json
import hashlib
from pathlib import Path
import sqlite3
import uuid

STAGES = ('Backlog', 'Ready', 'Needs input', 'Coding', 'QA', 'Review', 'Human review', 'Blocked', 'Done')


class NativeBoard:
    native = True
    def __init__(self, config):
        self.config = config
        self.path = Path(config['state_dir']) / 'kodi.sqlite'
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS dashboard_tasks (
                id TEXT PRIMARY KEY, external_id TEXT UNIQUE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dashboard_meta (key TEXT PRIMARY KEY, data TEXT NOT NULL);''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def bootstrap(self):
        with self.connection() as db:
            row = db.execute("SELECT data FROM dashboard_meta WHERE key='settings'").fetchone()
        if row:
            return json.loads(row[0])
        return {'workspace': 'kodi', 'workflow': 'delivery',
                'steps': {name: name.lower().replace(' ', '-') for name in STAGES},
                'repository_ids': {key: 'repo-'+key for key in self.config['repos']}}

    @staticmethod
    def validate(title, description):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 512:
            raise ValueError('Title must contain 1–512 characters')
        if not isinstance(description, str) or not description.strip() or len(description) > 64000:
            raise ValueError('Description must contain 1–64000 characters')

    def create(self, settings, title, description, external_id, repository_id=None):
        self.validate(title, description)
        task = {'id': str(uuid.uuid4()), 'title': title.strip()[:60], 'description': description,
                'external_id': external_id, 'workflow_id': settings['workflow'],
                'workspace_id': settings['workspace'], 'workflow_step_id': settings['steps']['Backlog'],
                'repositories': [{'repository_id': repository_id}] if repository_id else [],
                'start_agent': False, 'prepare_session': False, 'autopilot': False}
        with self.connection() as db:
            if external_id:
                existing = db.execute('SELECT data FROM dashboard_tasks WHERE external_id=?', (external_id,)).fetchone()
                if existing:
                    return json.loads(existing[0])
            db.execute('INSERT INTO dashboard_tasks VALUES (?,?,?)', (task['id'], external_id, json.dumps(task)))
        return task

    def tasks(self, settings):
        with self.connection() as db:
            rows = db.execute('SELECT data FROM dashboard_tasks ORDER BY rowid').fetchall()
        return [task for row in rows if (task := json.loads(row[0])).get('workflow_id') == settings['workflow']]

    def repositories(self, settings):
        ids = settings.get('repository_ids', {})
        return [{'id': ids.get(key, 'repo-'+key), 'name': key,
                 'github_url': 'https://github.com/'+repo['github_repo'] if repo.get('github_repo') else ''}
                for key, repo in self.config['repos'].items()]

    def request(self, path, data=None, method=None):
        parts = path.strip('/').split('/')
        if len(parts) != 2 or parts[0] != 'tasks':
            raise ValueError('Unsupported native board operation')
        with self.connection() as db:
            row = db.execute('SELECT data FROM dashboard_tasks WHERE id=?', (parts[1],)).fetchone()
            if not row:
                raise ValueError('Task not found')
            task = json.loads(row[0])
            if data is not None:
                if method not in (None, 'PATCH') or not set(data) <= {'title', 'description', 'workflow_step_id'}:
                    raise ValueError('Unsupported task fields')
                task.update(data)
                self.validate(task['title'], task['description'])
                if task['workflow_step_id'] not in self.bootstrap()['steps'].values():
                    raise ValueError('Unknown delivery stage')
                db.execute('UPDATE dashboard_tasks SET data=? WHERE id=?', (json.dumps(task), task['id']))
            return task

    def description(self, task_id, text):
        return self.request('/tasks/'+task_id, {'description': text}, method='PATCH')

    def move(self, settings, task_id, name):
        if name not in settings['steps']:
            raise ValueError('Unknown delivery stage')
        return self.request('/tasks/'+task_id, {'workflow_step_id': settings['steps'][name]}, method='PATCH')

    def activity_supported(self, workspace, timeout):
        # Native Stats reads the existing ledger directly; no export/copy is needed.
        return False

    @staticmethod
    def import_plan(settings, tasks):
        known = set(settings['steps'].values())
        seen, external = set(), set()
        for task in tasks:
            NativeBoard.validate(task['title'], task['description'])
            identity = task.get('id')
            if (not isinstance(identity, str) or not identity or identity in seen
                    or task.get('workflow_step_id') not in known
                    or task.get('workflow_id', settings['workflow']) != settings['workflow']):
                raise ValueError('Ambiguous task identity, workflow or stage')
            if task.get('external_id'):
                if task['external_id'] in external:
                    raise ValueError('Duplicate source identity; reconcile before importing')
                external.add(task['external_id'])
            seen.add(identity)
        return {'tasks': len(tasks), 'applied': False, 'identities_preserved': True}

    def import_tasks(self, settings, tasks, *, apply=False):
        """Copy legacy identities without changing their source or coordinator jobs."""
        result = self.import_plan(settings, tasks)
        if apply:
            snapshot = {'settings': settings, 'tasks': sorted(tasks, key=lambda t: t['id'])}
            digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
            with self.connection() as db:
                prior = db.execute("SELECT data FROM dashboard_meta WHERE key='import_digest'").fetchone()
                if prior and prior[0] == digest:
                    return {**result, 'applied': True, 'already_imported': True}
                if db.execute('SELECT 1 FROM dashboard_tasks LIMIT 1').fetchone():
                    raise ValueError('Import requires an empty native board; existing work is preserved')
                for task in tasks:
                    safe = {**task, 'workflow_id': settings['workflow'], 'workspace_id': settings['workspace'],
                            'start_agent': False, 'prepare_session': False, 'autopilot': False}
                    db.execute('INSERT INTO dashboard_tasks VALUES (?,?,?)',
                               (safe['id'], safe.get('external_id') or None, json.dumps(safe)))
                db.execute('INSERT OR REPLACE INTO dashboard_meta VALUES (?,?)', ('settings', json.dumps(settings)))
                db.execute('INSERT OR REPLACE INTO dashboard_meta VALUES (?,?)', ('import_digest', digest))
            result['applied'] = True
        return result

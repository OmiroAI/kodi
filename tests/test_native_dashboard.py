import json
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kodi.coordinator import Coordinator
from kodi.dashboard import Dashboard, handler, statistics
from kodi.native_board import NativeBoard


class NativeDashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = {'state_dir': temporary.name, 'board_backend': 'native',
                       'repos': {'project': {'enabled': True, 'github_repo': 'ExampleOrg/project'},
                                 'disabled': {'enabled': False}}}
        self.c = Coordinator(self.config)
        self.addCleanup(self.c.store.db.close)
        self.app = Dashboard(self.config)

    def draft(self, title='Independent draft'):
        return self.app.mutate('/api/tasks', {'title': title, 'repo': 'project', 'description': 'Fix the requested issue.'})

    def track(self, task, state='queued'):
        self.c.board.move(self.config['board'], task['id'], 'Ready')
        job = self.c.store.enqueue('test:'+task['id'], 'project', task['title'], task['description'], task['id'])
        self.c.store.update(job['id'], state=state)
        return self.c.store.get(job['id'])

    def test_edit_and_create_remain_independent_after_reload(self):
        first = self.draft('First ticket')
        self.app.mutate('/api/tasks/'+first['id'], {'title': 'Edited first', 'repo': 'project', 'description': 'Only the first ticket.'})
        second = self.draft('Second ticket')
        reopened = NativeBoard(self.config)
        self.assertIn('Only the first', reopened.request('/tasks/'+first['id'])['description'])
        self.assertNotIn('Only the first', reopened.request('/tasks/'+second['id'])['description'])
        self.assertEqual(reopened.request('/tasks/'+second['id'])['title'], 'Second ticket')

    def test_ready_uses_existing_scheduler_intake(self):
        task = self.draft()
        self.app.mutate('/api/tasks/'+task['id']+'/stage', {'stage': 'Ready'})
        with patch('kodi.coordinator.github.authenticated', return_value=True), \
                patch('kodi.coordinator.github.issues', return_value=[]), \
                patch('kodi.coordinator.github.create_issue', return_value='https://github.com/ExampleOrg/project/issues/1'):
            self.c.sync()
        row = self.c.store.db.execute('SELECT * FROM jobs WHERE board_id=?', (task['id'],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['state'], 'queued')
        self.assertEqual(row['requirements'], task['description'])

    def test_operator_cannot_invent_acceptance_or_enable_repository(self):
        task = self.draft()
        for stage in ('QA', 'Human review', 'Done'):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                self.app.mutate('/api/tasks/'+task['id']+'/stage', {'stage': stage})
        disabled = self.app.mutate('/api/tasks', {'title': 'Disabled draft', 'repo': 'disabled', 'description': 'Wait for authorization.'})
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.app.mutate('/api/tasks/'+disabled['id']+'/stage', {'stage': 'Ready'})
        self.assertFalse(self.config['repos']['disabled']['enabled'])

    def test_terminal_delivery_updates_native_stage_and_retains_requirements(self):
        task = self.draft()
        job = self.track(task, 'human_review')
        self.c.store.update(job['id'], pr_url='https://github.com/ExampleOrg/project/pull/1')
        self.c.finish_pr(self.c.store.get(job['id']), 'MERGED')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'done')
        saved = self.c.board.request('/tasks/'+task['id'])
        self.assertEqual(saved['workflow_step_id'], self.config['board']['steps']['Done'])
        self.assertEqual(saved['description'], task['description'])
        self.assertIsNotNone(self.c.store.db.execute(
            "SELECT 1 FROM notifications WHERE event=? AND delivered=1",
            ('board:terminal:'+str(job['id'])+':done',)).fetchone())

    def test_scope_edits_are_audited_before_execution_and_rejected_after(self):
        task = self.draft()
        job = self.track(task)
        self.app.mutate('/api/tasks/'+task['id'], {'title': 'Corrected scope', 'repo': 'project', 'description': 'New owner instructions.'})
        self.assertIn('New owner instructions', self.c.store.get(job['id'])['requirements'])
        self.assertIsNotNone(self.c.store.db.execute("SELECT 1 FROM evidence WHERE job=? AND kind='owner_scope_revision'", (job['id'],)).fetchone())
        self.c.store.update(job['id'], state='coding', attempt=1)
        with self.assertRaisesRegex(ValueError, 'Execution evidence'):
            self.app.mutate('/api/tasks/'+task['id'], {'title': 'Unsafe edit', 'repo': 'project', 'description': 'Overwrite active scope.'})
        self.assertIn('New owner instructions', self.c.store.get(job['id'])['requirements'])

    def test_pause_resume_uses_checkpoint_digest_and_preserves_budgets(self):
        task = self.draft()
        job = self.track(task)
        self.c.store.reserve_recovery(job['id'], 'test', 'candidate', 2)
        digest = self.app.detail(job['id'])['control']['digest']
        self.app.mutate('/api/jobs/'+str(job['id'])+'/control', {'action': 'pause', 'digest': digest})
        self.assertEqual(self.c.store.get(job['id'])['state'], 'paused')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            self.app.mutate('/api/jobs/'+str(job['id'])+'/control', {'action': 'resume', 'digest': digest})
        digest = self.app.detail(job['id'])['control']['digest']
        self.app.mutate('/api/jobs/'+str(job['id'])+'/control', {'action': 'resume', 'digest': digest})
        self.assertEqual(self.c.store.get(job['id'])['state'], 'queued')
        self.assertEqual(self.c.store.db.execute('SELECT attempts FROM recovery_attempts WHERE job=?', (job['id'],)).fetchone()[0], 1)

    def test_import_preserves_ids_and_refuses_to_overwrite_native_work(self):
        settings = self.config['board']
        source = [{'id': 'legacy-task', 'title': 'Imported task', 'description': 'Repo: project\n\nExisting scope.',
                   'workflow_id': settings['workflow'], 'workspace_id': settings['workspace'],
                   'workflow_step_id': settings['steps']['Ready'], 'external_id': 'legacy:1'}]
        before = self.c.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        self.assertFalse(self.c.board.import_tasks(settings, source)['applied'])
        self.assertEqual(self.c.board.tasks(settings), [])
        self.c.board.import_tasks(settings, source, apply=True)
        self.assertEqual(self.c.board.tasks(settings)[0]['id'], 'legacy-task')
        self.assertEqual(self.c.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], before)
        self.assertTrue(self.c.board.import_tasks(settings, source, apply=True)['already_imported'])
        with self.assertRaises(ValueError):
            self.c.board.import_tasks(settings, [{**source[0], 'title': 'Changed source'}], apply=True)

    def test_stats_filters_membership_before_pagination_and_retains_zero_unknown_qa(self):
        end = datetime(2026, 3, 8, tzinfo=timezone.utc)
        jobs = [self.c.store.enqueue('stats:'+str(i), 'project', 'Job '+str(i), 'Requirements') for i in range(5)]
        def usage(job, created, data):
            with self.c.store.db:
                self.c.store.db.execute('INSERT INTO usage(job,provider,model,data,seconds,created,phase,outcome) VALUES (?,?,?,?,?,?,?,?)',
                    (job['id'], 'codex', 'default', json.dumps(data), 1, created, 'coding', 'succeeded'))
        usage(jobs[0], (end-timedelta(days=8)).isoformat(), {'input_tokens': 100, 'output_tokens': 100})
        usage(jobs[1], (end-timedelta(days=1)).isoformat(), {'input_tokens': 0, 'output_tokens': 0})
        usage(jobs[2], (end-timedelta(days=2)).isoformat(), {'usage_unknown': True})
        usage(jobs[2], (end-timedelta(days=2,hours=-1)).isoformat(), {'input_tokens': 11, 'output_tokens': 13})
        outside = (end-timedelta(days=7,hours=1)).astimezone(timezone(timedelta(hours=2)))
        usage(jobs[0], outside.isoformat(), {'input_tokens': 100, 'output_tokens': 100})
        usage(jobs[4], end.isoformat(), {'input_tokens': 1, 'output_tokens': 1})
        self.c.store.evidence(jobs[3]['id'], 'a'*40, 'qa', {'passed': True})
        with self.c.store.db:
            self.c.store.db.execute('UPDATE evidence SET created=? WHERE job=?', ((end-timedelta(days=3)).isoformat(),jobs[3]['id']))
        result = statistics(self.c.store, days=7, limit=1, now=end)
        self.assertEqual(result['task_count'], 3)
        self.assertEqual(result['tasks'][0]['id'], jobs[1]['id'])
        self.assertEqual(result['next_offset'], 1)
        self.assertIsNone(result['providers']['codex']['total_tokens'])
        self.assertEqual(result['known_tokens'], 24)
        self.assertEqual(result['unknown_runs'], 1)
        self.assertEqual(result['qa_runs'], 1)
        qa_only = statistics(self.c.store, days=7, provider='qa', now=end)
        self.assertEqual([t['id'] for t in qa_only['tasks']], [jobs[3]['id']])
        self.assertEqual(qa_only['providers'], {})

    def test_http_auth_origin_and_static_path_boundary(self):
        token = 'x'*40
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.app, token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:'+str(server.server_port)
        with self.assertRaises(HTTPError) as denied:
            urlopen(base+'/api/state')
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        with urlopen(Request(base+'/api/state', headers={'Authorization': 'Bearer '+token})) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('frame-ancestors', response.headers['Content-Security-Policy'])
        with urlopen(Request(base+'/api/state', headers={'Authorization': 'Basic proxy-auth', 'X-Kodi-Token': token})) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(HTTPError) as denied:
            urlopen(Request(base+'/api/tasks', data=b'{}', headers={'Content-Type':'application/json',
                    'Authorization':'Bearer '+token, 'Origin':'https://example.invalid'}))
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        with self.assertRaises(HTTPError) as denied:
            urlopen(Request(base+'/../config.json', headers={'Authorization':'Bearer '+token}))
        denied.exception.close()

    def test_demo_does_not_read_operator_config(self):
        from kodi.cli import main
        with patch('sys.argv', ['kodi', '--config', '/unavailable-config.json', 'dashboard', '--demo']), \
                patch('kodi.dashboard.serve') as serve:
            main()
        config = serve.call_args.args[0]
        self.assertTrue(config['_dashboard_demo'])
        self.assertEqual(config['board_backend'], 'native')
        self.assertTrue(serve.call_args.kwargs['demo'])

    def test_import_accepts_long_legacy_titles_without_losing_identity(self):
        settings = self.config['board']
        title = 'Legacy title ' * 8
        source = [{'id': 'long-title-task', 'title': title, 'description': 'Existing legacy description',
                   'workflow_step_id': settings['steps']['Backlog']}]
        self.c.board.import_tasks(settings, source, apply=True)
        self.assertEqual(self.c.board.tasks(settings)[0]['title'], title)
        self.c.board.description('long-title-task', 'Updated host projection')
        self.assertEqual(self.c.board.request('/tasks/long-title-task')['title'], title)
        with self.assertRaises(ValueError):
            self.app.mutate('/api/tasks', {'title': title, 'repo': 'project', 'description': 'New draft'})

    def test_native_progress_does_not_copy_runtime_status_into_requirements(self):
        task = self.draft()
        job = self.track(task)
        self.assertTrue(self.c.progress(job))
        self.assertEqual(self.c.board.request('/tasks/'+task['id'])['description'], job['requirements'])
        self.assertNotIn('Kodi status:', self.c.board.request('/tasks/'+task['id'])['description'])

    def test_embedded_dashboard_is_opt_in_and_uses_coordinator_config(self):
        from kodi.dashboard import start_embedded
        with patch('kodi.dashboard.create_server') as create:
            self.assertIsNone(start_embedded(self.config))
            create.assert_not_called()
            self.config['dashboard'] = {'enabled': True, 'port': 8810}
            server = start_embedded(self.config)
            self.assertIs(server, create.return_value)
            self.assertIs(create.call_args.args[0], self.config)
            create.assert_called_once_with(self.config, '127.0.0.1', 8810)

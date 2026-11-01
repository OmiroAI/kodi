import json
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kodi.store import Store
from kodi.host_recovery import preview, authorize, apply, track
from kodi.coordinator import Coordinator
from kodi.cli import main


class HostRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        store = Store(root/'state.sqlite')
        self.addCleanup(store.db.close)
        self.c = SimpleNamespace(store=store, config={'state_dir':str(root), 'repos':{
            'example':{'enabled':True, 'source':str(root/'source'), 'github_repo':'ExampleOrg/project', 'qa_suite':'system'}}},
            job_branch=lambda j: 'kodi/task-'+str(j['id']), board=Mock(), progress=Mock(), review=Mock())
        job = store.enqueue('fictional-issue', 'example', 'Example', 'Example requirements')
        self.id = job['id']
        store.update(self.id, state='blocked', commit_sha='a'*40, pr_url='https://github.com/ExampleOrg/project/pull/1', attempt=2)
        store.evidence(self.id, 'a'*40, 'dispatch', {'stage':'review', 'requirements':'Example requirements',
            'base':'b'*40, 'board_id':None, 'external_id':'fictional-issue', 'branch':'kodi/task-1'})
        store.evidence(self.id, 'a'*40, 'author', {'test_steps':['Inspect example']})
        store.evidence(self.id, 'a'*40, 'qa', {'passed':True})
        self.git = patch('kodi.host_recovery.git', side_effect=self.read_git).start()
        self.addCleanup(patch.stopall)
        patch('kodi.host_recovery.github.remote_head', side_effect=lambda repo, branch: 'b'*40 if branch == 'main' else 'a'*40).start()
        patch('kodi.submission.validate_pull').start()
        patch('kodi.host_recovery.github.pull', return_value={}).start()

    def read_git(self, path, *args):
        if args[0] == 'status': return ''
        if args[0] == 'symbolic-ref': return 'kodi/task-1'
        return 'a'*40 if args[-1] == 'HEAD' else 'b'*40

    def test_preview_inventories_current_requirements_without_database_writes(self):
        before = self.c.store.db.total_changes
        plan = preview(self.c, self.id, 'review')
        self.assertTrue(plan['resumable'])
        self.assertEqual(self.c.store.db.total_changes, before)
        self.assertEqual(plan['requirements'], 'Example requirements')
        self.assertNotIn('dispatch', plan)

    def test_preview_shows_changed_requirements_and_blocks_stale_dispatch(self):
        original = preview(self.c, self.id, 'review')
        self.c.store.update(self.id, requirements='Revised example acceptance criteria')
        before = self.c.store.db.total_changes
        plan = preview(self.c, self.id, 'review')
        self.assertEqual(plan['requirements'], 'Revised example acceptance criteria')
        self.assertNotEqual(plan['digest'], original['digest'])
        self.assertFalse(plan['resumable'])
        self.assertIn('Stale requirements', ' '.join(plan['blockers']))
        self.assertEqual(self.c.store.db.total_changes, before)
        with self.assertRaises(RuntimeError):
            authorize(self.c, self.id, 'review', original['digest'], 'Scoped owner instruction')
        self.assertEqual(self.c.store.db.total_changes, before)

    def test_cli_preview_uses_initialized_read_only_store_without_writes(self):
        root = Path(self.c.config['state_dir'])
        database = root/'kodi.sqlite'
        # Use a closed standalone database to detect preview initialization writes.
        connection = sqlite3.connect(database)
        try:
            self.c.store.db.backup(connection)
            connection.execute('PRAGMA journal_mode=DELETE')
        finally:
            connection.close()
        config = root/'example-config.json'
        config.write_text(json.dumps(self.c.config))
        before = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
        output = io.StringIO()
        with patch('sys.argv', ['kodi', '--config', str(config), 'recover', 'preview', str(self.id)]), redirect_stdout(output):
            main()
        plan = json.loads(output.getvalue())
        self.assertEqual(plan['resumable'], not bool(self.c.store.get(self.id)['board_id']))
        self.assertEqual(plan['sha'], 'a'*40)
        self.assertEqual(plan['requirements'], 'Example requirements')
        self.assertEqual({p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}, before)
        reader = Store(database, read_only=True)
        try:
            self.assertEqual(reader.get(self.id)['state'], 'blocked')
            with self.assertRaises(sqlite3.OperationalError):
                reader.update(self.id, state='queued')
        finally:
            reader.db.close()

    def board_job(self):
        self.c.config['board'] = {'workflow':'example-workflow', 'steps':{'Blocked':'blocked'}}
        self.c.store.update(self.id, board_id='example-task')
        dispatch = {'stage':'review', 'requirements':'Example requirements',
                    'base':'b'*40, 'board_id':'example-task',
                    'external_id':'fictional-issue', 'branch':'kodi/task-1'}
        self.c.store.evidence(self.id, 'a'*40, 'dispatch', dispatch)
        return {'id':'example-task', 'description':'Example requirements', 'workflow_step_id':'blocked'}

    def test_cli_board_preview_never_contacts_kandev(self):
        self.board_job()
        # Exercise the real CLI path, with the actual Board transport forbidden.
        with patch('kodi.board.urlopen', side_effect=AssertionError('Preview contacted Kandev')):
            self.test_cli_preview_uses_initialized_read_only_store_without_writes()
        plan = preview(self.c, self.id, 'review')
        self.assertFalse(plan['resumable'])
        self.assertTrue(plan['inspections'])
        self.c.board.tasks.assert_not_called()

    def test_board_authorization_and_claim_recheck_live_task_without_consuming_budget(self):
        task = self.board_job()
        digest = preview(self.c, self.id, 'review')['digest']
        before = self.c.store.db.total_changes
        for tasks in ([], [dict(task, workflow_step_id='backlog')],
                      [dict(task, description='Changed requirements')], [task, task]):
            self.c.board.tasks.return_value = tasks
            with self.assertRaises(RuntimeError):
                authorize(self.c, self.id, 'review', digest, 'Scoped owner instruction')
        self.assertEqual(self.c.store.db.total_changes, before)
        self.c.board.tasks.return_value = [task]
        authorize(self.c, self.id, 'review', digest, 'Scoped owner instruction')
        for tasks in ([dict(task, workflow_step_id='paused')],
                      [dict(task, description='Changed requirements')]):
            self.c.board.tasks.return_value = tasks
            with self.assertRaises(RuntimeError):
                apply(self.c, self.id, 'review', digest)
        self.assertEqual(self.c.store.db.execute('SELECT count(*) FROM recovery_attempts').fetchone()[0], 0)
        self.c.review.assert_not_called()

    def test_cli_preview_missing_database_does_not_initialize_state(self):
        root = Path(self.c.config['state_dir'])/'missing-state'
        config = Path(self.c.config['state_dir'])/'example-config.json'
        config.write_text(json.dumps(dict(self.c.config, state_dir=str(root))))
        with patch('sys.argv', ['kodi', '--config', str(config), 'recover', 'preview', str(self.id)]):
            with self.assertRaises(sqlite3.OperationalError):
                main()
        self.assertFalse(root.exists())

    def test_same_sha_review_preserves_qa_pr_attempt_and_replay_is_noop(self):
        store = self.c.store
        original = dict(store.get(self.id))
        def review(*args):
            store.evidence(self.id, 'a'*40, 'review', {'approved':True,'criteria_met':True,'simple':True,
                'author_family':'codex','reviewer_family':'gemini','blocking_findings':[]})
        self.c.review.side_effect = review
        digest = preview(self.c, self.id, 'review')['digest']
        authorize(self.c, self.id, 'review', digest, 'Scoped owner instruction example')
        apply(self.c, self.id, 'review', digest)
        apply(self.c, self.id, 'review', digest)
        self.assertEqual(self.c.review.call_count, 1)
        current = store.get(self.id)
        for key in ('attempt','commit_sha','pr_url'): self.assertEqual(current[key], original[key])
        self.assertEqual(current['state'], 'validated')
        self.assertEqual(store.db.execute("SELECT count(*) FROM evidence WHERE kind='qa'").fetchone()[0], 1)

    def test_dirty_remote_stale_and_exhausted_block(self):
        self.git.side_effect = lambda p,*a: 'dirty' if a[0]=='status' else self.read_git(p,*a)
        self.assertIn('Dirty checkout', ' '.join(preview(self.c,self.id,'review')['blockers']))
        self.git.side_effect = self.read_git
        with patch('kodi.host_recovery.github.remote_head', side_effect=RuntimeError('unknown')):
            self.assertIn('Unknown remote', ' '.join(preview(self.c,self.id,'review')['blockers']))
        self.c.store.update(self.id, requirements='Changed')
        self.assertIn('Stale requirements', ' '.join(preview(self.c,self.id,'review')['blockers']))
        self.c.store.reserve_recovery(self.id,'host-review','a'*40,2)
        self.c.store.reserve_recovery(self.id,'host-review','a'*40,2)
        self.assertIn('budget exhausted', ' '.join(preview(self.c,self.id,'review')['blockers']))

    def test_absent_authorization_pause_and_interrupted_claim_fail_closed(self):
        digest = preview(self.c,self.id,'review')['digest']
        with self.assertRaises(RuntimeError): apply(self.c,self.id,'review',digest)
        authorize(self.c,self.id,'review',digest,'Scoped owner instruction')
        self.c.store.update(self.id,state='paused')
        with self.assertRaises(RuntimeError): apply(self.c,self.id,'review',digest)
        self.c.store.update(self.id,state='blocked')
        self.c.store.evidence(self.id,'a'*40,'recovery_claim',{'digest':digest,'stage':'review'})
        self.assertTrue(apply(self.c,self.id,'review',digest)['inspection_required'])
        self.c.review.assert_not_called()

    def test_concurrent_apply_cannot_claim_and_restart_keeps_claim(self):
        import fcntl
        digest = preview(self.c,self.id,'review')['digest']
        authorize(self.c,self.id,'review',digest,'Scoped owner instruction')
        with (Path(self.c.config['state_dir'])/'delivery.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'slot is owned'):
                apply(self.c,self.id,'review',digest)
        self.assertIsNone(self.c.store.db.execute("SELECT id FROM evidence WHERE kind='recovery_claim'").fetchone())
        self.c.store.evidence(self.id,'a'*40,'recovery_claim',{'digest':digest,'stage':'review'})
        database = Path(self.c.config['state_dir'])/'state.sqlite'
        reopened = Store(database)
        self.addCleanup(reopened.db.close)
        self.c.store = reopened
        self.assertTrue(apply(self.c,self.id,'review',digest)['inspection_required'])
        self.c.review.assert_not_called()

    def test_deferred_48_legacy_and_authoritative_backlog_are_untouched(self):
        store = self.c.store
        with store.db:
            store.db.execute("UPDATE jobs SET id=48,state='deferred' WHERE id=?",(self.id,))
        before = dict(store.get(48))
        plan = preview(self.c,48,'review')
        self.assertFalse(plan['resumable'])
        with self.assertRaises(RuntimeError): apply(self.c,48,'review',plan['digest'])
        self.assertEqual(dict(store.get(48)),before)
        self.c.review.assert_not_called()

    def test_failed_review_retains_artifacts_and_consumes_budget(self):
        digest = preview(self.c,self.id,'review')['digest']
        authorize(self.c,self.id,'review',digest,'Scoped owner instruction')
        self.c.review.side_effect = RuntimeError('Reviewer unavailable')
        with self.assertRaises(RuntimeError): apply(self.c,self.id,'review',digest)
        apply(self.c,self.id,'review',digest)
        self.assertEqual(self.c.review.call_count,1)
        self.assertEqual(self.c.store.get(self.id)['attempt'],2)
        self.assertEqual(self.c.store.db.execute('SELECT attempts FROM recovery_attempts').fetchone()[0],1)

    def test_tracking_refreshes_original_board_task_without_scheduling(self):
        self.c.config['board'] = {'steps': {'Backlog': 'backlog'}}
        self.c.store.update(self.id, board_id='original-task', error='Reviewer unavailable; inspect retained artifacts')
        task = {'id': 'original-task', 'description': 'Example requirements', 'workflow_step_id': 'backlog'}
        self.c.board.tasks.return_value = [task]
        self.c.dependency_blockers = lambda job: []
        self.c._progress = lambda job: Coordinator._progress(self.c, job)
        self.c.progress.side_effect = lambda job: Coordinator.progress(self.c, job)
        before = dict(self.c.store.get(self.id))
        for _ in range(2):
            self.assertEqual(track(self.c, self.id), 'original-task')
        text = self.c.board.description.call_args.args[1]
        self.assertIn('Kodi status: blocked', text)
        self.assertIn('Needs attention: Reviewer unavailable', text)
        self.assertIn('Commit: '+ 'a'*40, text)
        self.assertIn(before['pr_url'], text)
        self.assertEqual(dict(self.c.store.get(self.id)), before)
        self.c.board.create.assert_not_called()
        self.c.board.move.assert_not_called()
        self.c.review.assert_not_called()

    def test_tracking_missing_or_changed_board_task_fails_without_writes(self):
        self.c.config['board'] = {}
        self.c.store.update(self.id, board_id='original-task')
        before = self.c.store.db.total_changes
        for tasks, message in (([], 'missing'), ([{'id':'original-task', 'description':'Changed requirements'}], 'requirements changed')):
            self.c.board.tasks.return_value = tasks
            with self.assertRaisesRegex(RuntimeError, message):
                track(self.c, self.id)
        self.assertEqual(self.c.store.db.total_changes, before)
        self.c.progress.assert_not_called()
        self.c.board.create.assert_not_called()
        self.c.board.move.assert_not_called()

    def test_internal_tracking_reuses_task_after_creation_and_refreshes(self):
        self.c.config['board'] = {}
        task = {'id':'tracking-task', 'external_id':'kodi-internal:'+str(self.id),
                'description':'Example requirements'}
        self.c.board.tasks.return_value = []
        self.c.board.create.return_value = task
        self.assertEqual(track(self.c, self.id), task['id'])
        self.c.board.tasks.return_value = [task]
        self.assertEqual(track(self.c, self.id), task['id'])
        self.c.board.create.assert_called_once()
        self.assertEqual(self.c.progress.call_count, 2)
        self.c.board.move.assert_not_called()

    def test_null_candidate_has_explicit_read_only_blocker_and_cannot_be_authorized(self):
        self.c.store.update(self.id, commit_sha=None)
        before = self.c.store.db.total_changes
        plan = preview(self.c, self.id, 'review')
        self.assertIsNone(plan['sha'])
        self.assertFalse(plan['resumable'])
        self.assertIn('Missing retained candidate SHA', ' '.join(plan['blockers']))
        self.assertEqual(before, self.c.store.db.total_changes)
        with self.assertRaises(RuntimeError):
            authorize(self.c, self.id, 'review', plan['digest'], 'Scoped owner instruction')
        self.assertEqual(before, self.c.store.db.total_changes)
        self.c.review.assert_not_called()

    def test_internal_tracking_refuses_changed_existing_requirements_without_writes(self):
        self.c.config['board'] = {}
        self.c.board.tasks.return_value = [{'id': 'tracking-task',
            'external_id': 'kodi-internal:'+str(self.id), 'description': 'Changed requirements'}]
        before = self.c.store.db.total_changes
        with self.assertRaisesRegex(RuntimeError, 'authoritative requirements changed'):
            track(self.c, self.id)
        self.assertEqual(before, self.c.store.db.total_changes)
        self.assertIsNone(self.c.store.get(self.id)['board_id'])
        self.c.board.create.assert_not_called()
        self.c.progress.assert_not_called()

    def test_new_tracking_task_must_retain_requested_backlog_state(self):
        self.c.config['board'] = {'steps': {'Backlog': 'backlog-step'}}
        self.c.board.tasks.return_value = []
        self.c.board.create.return_value = {'id': 'tracking-task',
            'external_id': 'kodi-internal:'+str(self.id), 'description': 'Example requirements',
            'workflow_step_id': 'ready-step'}
        before = self.c.store.db.total_changes
        with self.assertRaisesRegex(RuntimeError, 'not in Backlog'):
            track(self.c, self.id)
        self.assertEqual(before, self.c.store.db.total_changes)
        self.assertIsNone(self.c.store.get(self.id)['board_id'])
        self.c.progress.assert_not_called()
        self.c.board.move.assert_not_called()

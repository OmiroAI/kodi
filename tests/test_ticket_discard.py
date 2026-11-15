"""Focused discard/restore regressions in disposable native dashboard state."""
import json
import unittest
from unittest.mock import patch

import test_native_dashboard as dashboard_tests
from kodi.store import Store, StaleClaim
from kodi.native_board import STAGES


class TicketDiscardTests(unittest.TestCase):
    setUp = dashboard_tests.NativeDashboardTests.setUp
    draft = dashboard_tests.NativeDashboardTests.draft
    track = dashboard_tests.NativeDashboardTests.track
    def action(self, task, action):
        return self.app.mutate('/api/tasks/'+task['id']+'/'+action, {})

    def test_drafts_from_every_board_stage_are_recoverable(self):
        for stage in STAGES:
            if stage == 'Discarded':
                continue
            with self.subTest(stage=stage):
                task = self.draft(stage)
                self.c.board.move(self.config['board'], task['id'], stage)
                before = self.c.board.request('/tasks/'+task['id'])
                self.action(task, 'discard')
                self.assertEqual(self.app.state()['tasks'][-1]['stage'], 'Discarded')
                self.c.board.move(self.config['board'], task['id'], 'Ready')
                self.assertEqual(self.c.board.request('/tasks/'+task['id'])['workflow_step_id'], 'discarded')
                with self.assertRaises(ValueError):
                    self.app.mutate('/api/tasks/'+task['id']+'/stage', {'stage': 'Ready'})
                self.action(task, 'restore')
                after = self.c.board.request('/tasks/'+task['id'])
                for field in ('title', 'description', 'external_id', 'repositories', 'workflow_step_id'):
                    self.assertEqual(after[field], before[field])

    def test_jobs_discard_every_state_preserve_candidate_pr_and_evidence(self):
        states = ('queued','intake_pending','coding','qa','review','validated','publishing',
                  'ci_pending','ci_failed','human_review','merge_pending','merge_updating',
                  'blocked','needs_input','paused','review_wait','done','cancelled')
        for state in states:
            with self.subTest(state=state):
                task = self.draft(state)
                job = self.track(task, state)
                self.c.store.update(job['id'], commit_sha='a'*40, pr_url='https://github.com/ExampleOrg/project/pull/1')
                self.c.store.evidence(job['id'], 'a'*40, 'retained_test', {'history':'retained'})
                before = dict(self.c.store.get(job['id']))
                with patch('kodi.ticket_control.settle_merge'):
                    self.action(task, 'discard')
                    self.assertEqual(self.c.store.get(job['id'])['state'], 'discarded')
                    self.c.finish_pr(self.c.store.get(job['id']), 'MERGED')
                    self.assertEqual(self.c.store.get(job['id'])['state'], 'discarded')
                    with patch('kodi.coordinator.github.pr_state', return_value='OPEN'), patch('kodi.coordinator.github.pull') as remote:
                        self.c.finish_prs()
                        remote.assert_not_called()
                    self.action(task, 'restore')
                after = self.c.store.get(job['id'])
                for field in ('requirements','commit_sha','branch','pr_url','attempt','external_id'):
                    self.assertEqual(after[field], before[field])
                expected = ('paused' if state in ('qa','review') else 'blocked' if state in ('coding','publishing','merge_pending','merge_updating') else state)
                self.assertEqual(after['state'], expected)
                self.assertEqual(json.loads(self.c.store.db.execute("SELECT data FROM evidence WHERE job=? AND kind='retained_test'", (job['id'],)).fetchone()[0]), {'history':'retained'})

    def test_running_worker_is_fenced_and_restore_waits(self):
        task = self.draft()
        job = self.track(task, 'coding')
        token = self.c.store.begin_invocation(job['id'], 'coding', 'coding')
        worker = Store(self.c.board.path, token)
        self.addCleanup(worker.db.close)
        self.action(task, 'discard')
        with self.assertRaises(StaleClaim):
            worker.update(job['id'], state='qa')
        with self.assertRaisesRegex(ValueError, 'active or uncertain'):
            self.action(task, 'restore')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'discarded')
        with self.c.store.db:
            self.c.store.db.execute("UPDATE invocations SET status='stale' WHERE id=?", (token,))
        self.action(task, 'restore')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'blocked')

    def test_ready_draft_is_not_dispatched_while_discarded(self):
        task = self.draft()
        self.c.board.move(self.config['board'], task['id'], 'Ready')
        self.action(task, 'discard')
        with patch('kodi.coordinator.github.authenticated', return_value=True), patch('kodi.coordinator.github.issues', return_value=[]), patch('kodi.coordinator.github.create_issue') as create:
            self.c.sync()
            create.assert_not_called()
        self.assertIsNone(self.c.store.db.execute('SELECT * FROM jobs WHERE board_id=?', (task['id'],)).fetchone())

    def test_remote_failure_retains_discard_and_blocks_restore(self):
        task = self.draft()
        job = self.track(task, 'merge_pending')
        self.c.store.update(job['id'], pr_url='https://github.com/ExampleOrg/project/pull/1')
        with patch('kodi.ticket_control.github.pull', side_effect=RuntimeError('unavailable')):
            with self.assertRaisesRegex(ValueError, 'Discarded locally'):
                self.action(task, 'discard')
            with self.assertRaisesRegex(ValueError, 'uncertain'):
                self.action(task, 'restore')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'discarded')
        self.assertEqual(self.app.state()['tasks'][0]['stage'], 'Discarded')

    def test_remote_auto_merge_is_disarmed_before_restore(self):
        task = self.draft()
        job = self.track(task, 'human_review')
        self.c.store.update(job['id'], pr_url='https://github.com/ExampleOrg/project/pull/1')
        armed = {'state':'OPEN', 'autoMergeRequest':{}, 'number':1}
        idle = dict(armed, autoMergeRequest=None)
        with patch('kodi.ticket_control.github.pull', side_effect=[armed,idle,idle,idle]), patch('kodi.ticket_control.github.merge_queue_observation', return_value={'queued':False}), patch('kodi.ticket_control.github.gh') as gh:
            self.action(task, 'discard')
            gh.assert_called_once_with('pr','merge','https://github.com/ExampleOrg/project/pull/1','--disable-auto')
            self.action(task, 'restore')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'human_review')

    def test_restore_qa_requires_explicit_resume_of_retained_candidate(self):
        from kodi.job_control import inspect, control
        task = self.draft()
        job = self.track(task, 'qa')
        self.c.store.update(job['id'], commit_sha='a'*40, attempt=2)
        self.action(task, 'discard')
        self.action(task, 'restore')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'paused')
        plan = inspect(self.c.store, job['id'])
        control(self.c.store, job['id'], 'resume', plan['digest'], 'Disposable test')
        restored = self.c.store.get(job['id'])
        self.assertEqual((restored['state'], restored['commit_sha'], restored['attempt']), ('qa', 'a'*40, 2))

    def test_queue_removal_and_verification(self):
        task = self.draft()
        job = self.track(task, 'merge_pending')
        self.c.store.update(job['id'], pr_url='https://github.com/ExampleOrg/project/pull/1')
        idle = {'state':'OPEN', 'autoMergeRequest':None, 'number':1}
        with patch('kodi.ticket_control.github.pull', return_value=idle), patch('kodi.ticket_control.github.merge_queue_observation', side_effect=[{'queued':True},{'queued':False}]), patch('kodi.ticket_control.github.gh', side_effect=[json.dumps({'data':{'resource':{'id':'PR_example'}}}),json.dumps({'data':{}})]) as gh:
            self.action(task, 'discard')
            self.assertIn('dequeuePullRequest', gh.call_args_list[1].args[3])
        self.assertEqual(self.c.store.get(job['id'])['state'], 'discarded')

    def test_discarded_identity_drift_cannot_restore(self):
        task = self.draft()
        job = self.track(task)
        self.action(task, 'discard')
        self.c.store.update(job['id'], commit_sha='b'*40)
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.action(task, 'restore')

    def test_frontend_default_visibility_checkbox_and_both_layouts(self):
        import shutil
        import subprocess
        from pathlib import Path
        if not shutil.which('node'):
            self.skipTest('Node is unavailable')
        source = Path('kodi/dashboard_static/app.js').read_text()
        # Execute the real rendering/filter functions with a minimal DOM.
        script = """
const assert = require('assert');
const elements = new Map();
const document = {getElementById(id) {
 if (!elements.has(id)) elements.set(id, {innerHTML:'',classList:{toggle(){}},querySelectorAll(){return []}});
 return elements.get(id);
}};
""" + source.split("$('new-task').onclick")[0] + """
state.data = {tasks:[{id:'a',title:'Visible',stage:'Ready'}, {id:'b',title:'Hidden',stage:'Discarded'}]};
assert.deepEqual(visibleTasks().map(t=>t.id), ['a']);
renderBoard();
assert(!$('board').innerHTML.includes('Hidden'));
""" + source[source.index("$('show-discarded').onchange"):source.index("$('show-done').onchange")] + """
$('show-discarded').onchange({target:{checked:true}});
assert.deepEqual(visibleTasks().map(t=>t.id), ['a','b']);
assert($('board').innerHTML.includes('Hidden'));
state.list = true;
renderBoard();
assert($('board').innerHTML.includes('Hidden'));
$('show-discarded').onchange({target:{checked:false}});
assert(!$('board').innerHTML.includes('Hidden'));
"""
        subprocess.run(['node','-e',script], check=True, capture_output=True, text=True)

    def test_uncertain_capacity_requires_stopped_executor_attestation(self):
        import os
        from pathlib import Path
        from kodi.capacity import acquire
        from kodi.job_control import control, inspect
        task = self.draft()
        job = self.track(task, 'qa')
        token = self.c.store.begin_invocation(job['id'], 'checkpoint', 'qa')
        limits = {'stages':{'coding':1,'qa':1,'review':1},'providers':{'qa':1}}
        acquire(self.c.store, job['id'], 'qa', 'qa', limits, 'test-owner')
        self.c.store.evidence(job['id'], '', 'executor_identity', {'token':token,'pid':os.getpid(), 'start':Path('/proc/self/stat').read_text().split()[21]})
        self.action(task, 'discard')
        with self.assertRaisesRegex(ValueError, 'still alive'):
            control(self.c.store, job['id'], 'reconcile', inspect(self.c.store,job['id'])['digest'], 'remote-completion:fixture')
        with self.c.store.db:
            self.c.store.db.execute("UPDATE invocations SET status='stale' WHERE id=?", (token,))
        self.c.store.evidence(job['id'], '', 'executor_identity', {'token':token,'pid':999999999,'start':'absent'})
        with self.assertRaisesRegex(ValueError, 'inspection reference'):
            control(self.c.store, job['id'], 'reconcile', inspect(self.c.store,job['id'])['digest'], 'fixture')
        control(self.c.store, job['id'], 'reconcile', inspect(self.c.store,job['id'])['digest'], 'remote-completion:fixture')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'discarded')
        self.assertFalse(inspect(self.c.store,job['id'])['capacity'])
        self.action(task, 'restore')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'paused')

    def test_discarded_merge_intent_does_not_request_merge(self):
        task = self.draft()
        job = self.track(task, 'merge_pending')
        self.c.config['repos']['project']['auto_merge'] = True
        self.c.store.evidence(job['id'], '', 'merge_maintenance', {'expected_head':'a'*40})
        self.action(task, 'discard')
        with patch('kodi.coordinator.github.authenticated', return_value=True), patch('kodi.coordinator.github.pull') as remote, patch('kodi.coordinator.github.auto_merge') as merge:
            self.c.maintain_merges()
            remote.assert_not_called()
            merge.assert_not_called()
        self.assertTrue(self.c.store.delivery_ownership(self.c.store.get(job['id']))['active'])

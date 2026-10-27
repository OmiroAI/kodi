import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kodi.store import Store
from kodi.host_recovery import preview, authorize, apply


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

    def test_preview_has_no_database_writes_and_hides_requirements(self):
        before = self.c.store.db.total_changes
        plan = preview(self.c, self.id, 'review')
        self.assertTrue(plan['resumable'])
        self.assertEqual(self.c.store.db.total_changes, before)
        self.assertNotIn('Example requirements', json.dumps(plan))

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

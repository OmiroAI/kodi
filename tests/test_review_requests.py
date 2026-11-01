import json
import tempfile
import unittest
from unittest.mock import patch
from kodi import github
from kodi.coordinator import Coordinator

SHA='a'*40
URL='https://github.com/owner/repo/pull/1'


class RequestTests(unittest.TestCase):
    def test_rerequests_dismissed_review_and_avoids_duplicate_request(self):
        pr={'state':'open','head':{'sha':SHA},'user':{'login':'bot'},'requested_reviewers':[]}
        values=[json.dumps(pr),json.dumps([{'state':'DISMISSED','user':{'login':'owner'},'commit_id':'b'*40}]),'{}']
        with patch('kodi.github.gh',side_effect=values) as gh:
            github.request_reviewers(URL,SHA,['owner'])
        self.assertEqual(json.loads(gh.call_args.kwargs['stdin']),{'reviewers':['owner']})
        for pending,reviews in (([{'login':'owner'}],[]),([],[{'state':'APPROVED','user':{'login':'owner'},'commit_id':SHA}])):
            pr['requested_reviewers']=pending
            with patch('kodi.github.gh',side_effect=[json.dumps(pr),json.dumps(reviews)]) as gh:
                github.request_reviewers(URL,SHA,['owner'])
            self.assertEqual(gh.call_count,2)

    def test_changed_head_never_requests_review(self):
        with patch('kodi.github.gh',return_value=json.dumps({'state':'open','head':{'sha':'b'*40}})) as gh:
            with self.assertRaises(RuntimeError):github.request_reviewers(URL,SHA,['owner'])
        self.assertEqual(gh.call_count,1)

    def test_delivery_retries_after_restart_and_again_for_new_commit(self):
        with tempfile.TemporaryDirectory() as d:
            config={'state_dir':d,'repos':{'repo':{}},'reviewers':['owner']}
            c=Coordinator(config);job=c.store.enqueue('1','repo','Task','Requirements')
            c.store.update(job['id'],state='human_review',pr_url=URL,commit_sha=SHA)
            with patch.object(c,'approval_candidate',return_value=('base',False)),patch('kodi.github.request_reviewers',side_effect=RuntimeError('offline')):
                c.request_reviews()
            self.assertEqual(c.store.get(job['id'])['state'],'human_review');c.store.db.close()
            c=Coordinator(config)
            with patch.object(c,'approval_candidate',return_value=('base',False)),patch('kodi.github.request_reviewers') as request:
                c.request_reviews();c.request_reviews()
                self.assertEqual(request.call_count,1)
                c.store.update(job['id'],commit_sha='b'*40);c.request_reviews()
                self.assertEqual(request.call_count,2)
            c.store.db.close()

    def test_board_and_notification_outage_cannot_prevent_github_request(self):
        with tempfile.TemporaryDirectory() as d:
            c=Coordinator({'state_dir':d,'repos':{'repo':{}},'reviewers':['owner']})
            job=c.store.enqueue('1','repo','Task','Requirements')
            c.store.update(job['id'],state='human_review',pr_url=URL,commit_sha=SHA)
            with patch.object(c,'approval_candidate',return_value=('base',False)), \
                    patch.object(c,'move',side_effect=RuntimeError('board offline')), \
                    patch.object(c,'notification',side_effect=RuntimeError('chat offline')), \
                    patch('kodi.github.request_reviewers') as request:
                c.request_reviews()
            request.assert_called_once_with(URL,SHA,['owner']);c.store.db.close()

class StableCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = Coordinator({'state_dir': self.temp.name, 'repos': {'repo': {}},
                              'reviewers': ['owner']})
        self.addCleanup(self.c.store.db.close)
        job = self.c.store.enqueue('1', 'repo', 'Task', 'Requirements')
        self.c.store.update(job['id'], state='human_review', pr_url=URL, commit_sha=SHA)
        self.job = self.c.store.get(job['id'])

    def test_gate_order_and_unavailable_ci_block_all_delivery(self):
        from contextlib import ExitStack
        pr = {'state': 'OPEN', 'isDraft': False, 'headRefOid': SHA}
        with ExitStack() as stack:
            gate = stack.enter_context(patch.object(self.c.store, 'gate', return_value=False))
            stack.enter_context(patch('kodi.github.pull', return_value=pr))
            stack.enter_context(patch.object(self.c, 'approval_base', return_value='base'))
            stack.enter_context(patch.object(self.c, 'validate_merge_head'))
            ci = stack.enter_context(patch.object(self.c, 'check_outcome', return_value='passed'))
            stack.enter_context(patch.object(self.c, 'human_approved', return_value=False))
            notice = stack.enter_context(patch.object(self.c, 'notification'))
            request = stack.enter_context(patch('kodi.github.request_reviewers'))
            self.c.request_reviews()
            ci.assert_not_called()
            self.assertIn('QA/source review', self.c.store.get(self.job['id'])['error'])
            gate.return_value = True
            for outcome in ('pending', 'failed'):
                ci.return_value = outcome
                self.c.request_reviews()
                self.assertIn('CI', self.c.store.get(self.job['id'])['error'])
            ci.side_effect = RuntimeError('CI inspection unavailable')
            self.c.request_reviews()
            self.assertIn('CI inspection unavailable', self.c.store.get(self.job['id'])['error'])
            notice.assert_not_called(); request.assert_not_called()
            ci.side_effect = None; ci.return_value = 'passed'
            self.c.request_reviews(); self.c.request_reviews()
            request.assert_called_once()

    def test_approved_then_revoked_renews_once_and_base_identity_is_distinct(self):
        with patch.object(self.c, 'approval_candidate', return_value=('base', False)) as candidate, \
                patch.object(self.c, 'notification') as notice, \
                patch('kodi.github.request_reviewers') as request:
            self.c.request_reviews(); self.c.request_reviews()
            self.assertEqual(request.call_count, 1)
            candidate.return_value = ('base', True)
            self.c.request_reviews()
            self.assertEqual(request.call_count, 1)
            candidate.return_value = ('base', False)
            self.c.request_reviews(); self.c.request_reviews()
            self.assertEqual(request.call_count, 2)
            candidate.return_value = ('new-base', False)
            self.c.request_reviews()
            self.assertEqual(request.call_count, 3)
            self.assertIn('base base -> new-base', notice.call_args.args[1])

    def test_drift_after_owner_delivery_prevents_review_request(self):
        with patch.object(self.c, 'approval_candidate', side_effect=[
                ('base', False), ('base', False), RuntimeError('Head drift')]), \
                patch.object(self.c, 'notification') as notice, \
                patch('kodi.github.request_reviewers') as request:
            self.c.request_reviews()
            notice.assert_called_once(); request.assert_not_called()
            self.assertIn('Head drift', self.c.store.get(self.job['id'])['error'])

    def test_base_and_head_drift_fail_before_ci_or_approval(self):
        from types import SimpleNamespace
        pr = {'headRefOid': 'b'*40}
        with self.assertRaisesRegex(RuntimeError, 'previous QA.*stale'):
            self.c.approval_base(self.job, pr)
        pr['headRefOid'] = SHA
        with patch.object(self.c, 'validate_merge_head', return_value=self.temp.name), \
                patch.object(self.c, 'refresh_source'), \
                patch.object(self.c, 'fetch_verified_base', return_value='new-base'), \
                patch('kodi.coordinator.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'fresh base validation'):
                self.c.approval_base(self.job, pr)

    def test_uncertain_owner_delivery_is_not_replayed(self):
        with patch.object(self.c, 'approval_candidate', return_value=('base', False)), \
                patch.object(self.c, 'notification', side_effect=RuntimeError('receipt lost')) as notice, \
                patch('kodi.github.request_reviewers') as request:
            self.c.request_reviews(); self.c.request_reviews()
            notice.assert_called_once(); request.assert_called_once()
            self.assertIn('delivery uncertain', self.c.store.get(self.job['id'])['error'])

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
            with patch.object(c.store,'gate',return_value=True),patch('kodi.github.request_reviewers',side_effect=RuntimeError('offline')):
                c.request_reviews()
            self.assertEqual(c.store.get(job['id'])['state'],'human_review');c.store.db.close()
            c=Coordinator(config)
            with patch.object(c.store,'gate',return_value=True),patch('kodi.github.request_reviewers') as request:
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
            with patch.object(c.store,'gate',return_value=True), \
                    patch.object(c,'move',side_effect=RuntimeError('board offline')), \
                    patch.object(c,'notification',side_effect=RuntimeError('chat offline')), \
                    patch('kodi.github.request_reviewers') as request:
                c.request_reviews()
            request.assert_called_once_with(URL,SHA,['owner']);c.store.db.close()

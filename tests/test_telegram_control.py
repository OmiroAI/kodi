import json
import tempfile
import unittest
from unittest.mock import Mock,patch

from kodi.telegram_control import Control,approval_item

SHA='a'*40
URL='https://github.com/owner/project/pull/42'


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.config={'state_dir':self.temp.name,'telegram_env':'unused',
            'telegram':{'enabled':True,'allowed_user_ids':[42],'approver_user_id':42,'github_approver':'maintainer',
                        'whisper_url':'http://whisper-host:8008/v1/audio/transcriptions'},
            'reviewers':['maintainer'],'repos':{'project':{'github_repo':'owner/project','qa_suite':'project','auto_merge':True},
                                        'system':{'github_repo':'owner/dev-system','qa_suite':'system'}}}
        self.api=Mock();self.api.chat_id=42
        self.control=Control(self.config,self.api);self.addCleanup(lambda:self.control.close())

    def message(self,text=None,**extra):
        m={'message_id':7,'from':{'id':42,'is_bot':False},'chat':{'id':42,'type':'private'}}
        if text is not None:m['text']=text
        m.update(extra);return {'update_id':8,'message':m}

    def ticket(self):
        self.control.handle(self.message('project: Fix volume controls'))
        return self.control.state.db.execute("SELECT * FROM items WHERE kind='ticket'").fetchone()

    def ready(self):
        store=self.control.coordinator.store
        job=store.enqueue('issue','project','Fix','Requirements')
        store.update(job['id'],state='human_review',pr_url=URL,commit_sha=SHA)
        for kind,value in [('author',{'summary':'Changed controls','test_steps':['Press volume']}),('qa',{'passed':True}),
            ('review',{'approved':True,'criteria_met':True,'simple':True,'author_family':'openai','reviewer_family':'google'})]:
            store.evidence(job['id'],SHA,kind,value)
        return store.get(job['id'])

    def pr(self,sha=SHA):
        return {'state':'OPEN','isDraft':False,'number':42,'headRefOid':sha,'reviewDecision':'REVIEW_REQUIRED'}

    def test_wrong_user_chat_and_forwarded_message_have_no_effect(self):
        messages=[self.message('/approve project 42',**{'from':{'id':99}}),
                  self.message('project: task',chat={'id':99,'type':'private'}),
                  self.message('project: task',forward_origin={'type':'user'}),
                  self.message('/approve project 42',via_bot={'id':123})]
        with patch('kodi.owner_review.approve') as approve,patch('kodi.github.create_issue') as create:
            for update in messages:self.control.handle(update)
        approve.assert_not_called();create.assert_not_called();self.api.send.assert_not_called()
        self.assertEqual(self.control.state.db.execute('SELECT count(*) FROM items').fetchone()[0],0)

    def test_voice_transcript_cannot_become_an_approval_command(self):
        self.api.download_voice.return_value=b'ogg bytes'
        with patch('kodi.telegram_control.transcribe',return_value='/approve project 42'), \
                patch.object(self.control,'approve') as approve:
            self.control.handle(self.message(voice={'file_id':'file','duration':5}))
        approve.assert_not_called()
        row=self.control.state.db.execute("SELECT * FROM items WHERE kind='ticket'").fetchone()
        self.assertEqual(json.loads(row['data'])['body'],'/approve project 42')
        self.assertTrue(json.loads(row['data'])['voice'])
        self.assertIn('Voice ticket draft',self.api.send.call_args.args[0])

    def test_long_voice_is_rejected_before_download(self):
        self.control.handle(self.message(voice={'file_id':'file','duration':301}))
        self.api.download_voice.assert_not_called()

    def test_ticket_confirmation_and_replayed_updates_do_not_duplicate_issue(self):
        row=self.ticket();id=row['id'];url='https://github.com/owner/project/issues/9'
        with patch('kodi.github.create_issue',return_value=url) as create, \
                patch('kodi.github.gh',side_effect=[json.dumps({'labels':[]}),'{}']) as gh:
            self.control.callback(42,42,'create:'+id+':project')
            self.control.callback(42,42,'create:'+id+':project')
            self.control.handle(self.message('project: Fix volume controls'))
        create.assert_called_once();self.assertIn('kodi-telegram-ticket:'+id,create.call_args.args[2])
        self.assertEqual(gh.call_count,2)
        self.assertEqual(self.control.state.get(id,42,42)['state'],'done')

    def test_changed_repository_invalidates_old_create_button(self):
        row=self.ticket();self.control.callback(42,42,'repo:'+row['id']+':system')
        with patch('kodi.github.create_issue') as create,self.assertRaisesRegex(ValueError,'repository changed'):
            self.control.callback(42,42,'create:'+row['id']+':project')
        create.assert_not_called()

    def test_interrupted_issue_creation_reconciles_existing_marker_after_restart(self):
        row=self.ticket();id=row['id']
        with patch('kodi.github.create_issue',side_effect=RuntimeError('lost response')):
            with self.assertRaises(RuntimeError):self.control.callback(42,42,'create:'+id+':project')
        self.control.close();self.control=Control(self.config,self.api)
        with patch.object(self.control,'find_ticket',return_value='https://github.com/owner/project/issues/9'), \
                patch('kodi.github.gh',return_value=json.dumps({'labels':[{'name':'kodi:ready'}]})), \
                patch('kodi.github.create_issue') as create:
            self.control.callback(42,42,'create:'+id+':project')
        create.assert_not_called();self.assertEqual(self.control.state.get(id,42,42)['state'],'done')

    def test_unknown_creation_is_not_blindly_repeated(self):
        row=self.ticket();self.control.state.update(row['id'],state='creating')
        with patch.object(self.control,'find_ticket',return_value=None),patch('kodi.github.create_issue') as create:
            with self.assertRaisesRegex(ValueError,'could not be confirmed'):
                self.control.callback(42,42,'create:'+row['id']+':project')
        create.assert_not_called();self.assertEqual(self.control.state.get(row['id'],42,42)['state'],'uncertain')

    def test_wrong_owner_and_stale_sha_cannot_approve(self):
        job=self.ready();item=approval_item(self.config,self.control.state,job,42)
        with patch('kodi.owner_review.approve') as submit:
            with self.assertRaises(ValueError):self.control.approve(99,42,item['id'])
            self.control.coordinator.store.update(job['id'],commit_sha='b'*40)
            with self.assertRaisesRegex(ValueError,'changed'):self.control.approve(42,42,item['id'])
        submit.assert_not_called()

    def test_failed_checks_prevent_owner_approval(self):
        item=approval_item(self.config,self.control.state,self.ready(),42)
        with patch('kodi.github.pull',return_value=self.pr()), \
                patch.object(self.control.coordinator,'checks_pass',return_value=False), \
                patch('kodi.owner_review.approve') as submit:
            with self.assertRaisesRegex(ValueError,'checks'):self.control.approve(42,42,item['id'])
        submit.assert_not_called()

    def test_approval_is_bound_to_human_identity_and_sha_and_is_idempotent(self):
        job=self.ready();item=approval_item(self.config,self.control.state,job,42)
        review={'id':123,'state':'APPROVED','commit_id':SHA,'user':{'login':'maintainer'}}
        with patch('kodi.github.pull',return_value=self.pr()), \
                patch.object(self.control.coordinator,'checks_pass',return_value=True), \
                patch('kodi.github.approved_at',side_effect=[False,True]), \
                patch('kodi.owner_review.approve',return_value=review) as submit:
            self.control.approve(42,42,item['id']);self.control.approve(42,42,item['id'])
        submit.assert_called_once_with('owner/project',42,SHA,'maintainer',item['id'])
        self.assertEqual(self.control.state.get(item['id'],42,42)['state'],'done')
        fresh=approval_item(self.config,self.control.state,job,42)
        self.assertNotEqual(fresh['id'],item['id'])

    def test_concurrent_head_change_does_not_approve_new_candidate(self):
        item=approval_item(self.config,self.control.state,self.ready(),42)
        review={'id':123,'state':'APPROVED','commit_id':SHA,'user':{'login':'maintainer'}}
        with patch('kodi.github.pull',side_effect=[self.pr(),self.pr('b'*40)]), \
                patch.object(self.control.coordinator,'checks_pass',return_value=True), \
                patch('kodi.github.approved_at',return_value=False), \
                patch('kodi.owner_review.approve',return_value=review):
            with self.assertRaisesRegex(ValueError,'changed during'):self.control.approve(42,42,item['id'])
        self.assertEqual(self.control.state.get(item['id'],42,42)['state'],'stale')
        with patch('kodi.github.approved_at',return_value=False):
            self.assertFalse(self.control.coordinator.human_approved(self.ready(),{'reviewDecision':'APPROVED'}))

    def test_poll_skips_pre_feature_history_and_persists_offset(self):
        old={'update_id':9,'message':{'text':'/approve project 42'}};new=self.message('/help');new['update_id']=10
        self.api.call.side_effect=[[old],[new],[]]
        with patch.object(self.control,'handle') as handle:
            self.control.poll();self.control.poll()
        handle.assert_called_once_with(new)
        self.assertEqual(self.control.state.setting('offset'),'11')
        self.assertEqual(self.api.call.call_args.args[1]['offset'],11)

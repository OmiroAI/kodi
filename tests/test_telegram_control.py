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

    def test_refreshed_candidate_discovery_and_old_button_after_restart(self):
        old=self.ready();old_item=approval_item(self.config,self.control.state,old,42)
        self.control.state.update(old_item['id'],state='done')
        sha='b'*40;store=self.control.coordinator.store
        store.update(old['id'],commit_sha=sha)
        for kind in ('author','qa','review'):
            row=store.db.execute('SELECT data FROM evidence WHERE job=? AND kind=? ORDER BY id DESC LIMIT 1',(old['id'],kind)).fetchone()
            store.evidence(old['id'],sha,kind,json.loads(row[0]))
        self.control.close();self.control=Control(self.config,self.api)
        with patch('kodi.github.pull',return_value=self.pr(sha)), \
                patch.object(self.control.coordinator,'checks_pass',return_value=True), \
                patch('kodi.github.approved_at',return_value=False), \
                patch('kodi.owner_review.approve',return_value={'id':9,'state':'APPROVED','commit_id':sha,'user':{'login':'maintainer'}}) as submit:
            self.control.prs(42,42)
            text,markup=self.api.send.call_args.args
            self.assertIn(sha,text);self.assertIn('Changed controls',text)
            action=markup['inline_keyboard'][0][0]['callback_data']
            self.assertNotIn(old_item['id'],action)
            with self.assertRaisesRegex(ValueError,'changed'):self.control.callback(42,42,'approve:'+old_item['id'])
            self.control.callback(42,42,action)
            with self.assertRaisesRegex(ValueError,'could not be confirmed'):self.control.callback(42,42,action)
            submit.assert_called_once_with('owner/project',42,sha,'maintainer',action.split(':')[1])

    def test_prs_never_offers_button_before_current_gates(self):
        job=self.ready()
        with patch('kodi.github.pull',return_value=self.pr()), \
                patch.object(self.control.coordinator,'checks_pass',return_value=False):
            self.control.prs(42,42)
        self.assertEqual(len(self.api.send.call_args.args),1)
        self.assertIn('checks',self.api.send.call_args.args[0])
        self.control.coordinator.store.update(job['id'],commit_sha='b'*40)
        self.control.prs(42,42)
        self.assertIn('validation',self.api.send.call_args.args[0])
        self.assertEqual(len(self.api.send.call_args.args),1)

    def test_ready_notification_retry_expiry_restart_and_live_gates(self):
        job=self.ready();c=self.control.coordinator
        event='ready:'+str(job['id'])+':'+SHA
        with patch('kodi.telegram_control.TelegramAPI',return_value=self.api), \
                patch('kodi.github.pull',return_value=self.pr()), \
                patch.object(c,'checks_pass',return_value=True) as checks, \
                patch('kodi.github.approved_at',return_value=False), \
                patch('kodi.coordinator.send') as send:
            send.side_effect=RuntimeError('offline')
            with self.assertRaises(RuntimeError):c.notification(event,c.ready_text(job))
            send.side_effect=None
            c.notification(event,c.ready_text(job));c.notification(event,c.ready_text(job))
            self.assertEqual(send.call_count,2)
            item=approval_item(self.config,self.control.state,job,42)
            with self.control.state.db:self.control.state.db.execute('UPDATE items SET expires=0 WHERE id=?',(item['id'],))
            c.notification(event,c.ready_text(job))
            self.assertEqual(send.call_count,3)
            checks.return_value=False
            c.notification(event,c.ready_text(job));self.assertEqual(send.call_count,3)
        self.control.close();self.control=Control(self.config,self.api);c=self.control.coordinator
        with patch('kodi.telegram_control.TelegramAPI',return_value=self.api), \
                patch('kodi.github.pull',return_value=self.pr()), \
                patch.object(c,'checks_pass',return_value=True), \
                patch('kodi.github.approved_at',return_value=False), \
                patch('kodi.coordinator.send') as send:
            c.notification(event,c.ready_text(job));send.assert_not_called()

    def test_revoked_approval_requires_fresh_action(self):
        job=self.ready();item=approval_item(self.config,self.control.state,job,42)
        self.control.state.update(item['id'],state='done')
        with patch('kodi.github.pull',return_value=self.pr()), \
                patch.object(self.control.coordinator,'checks_pass',return_value=True), \
                patch('kodi.github.approved_at',return_value=False), \
                patch('kodi.owner_review.approve') as submit:
            with self.assertRaisesRegex(ValueError,'could not be confirmed'):
                self.control.callback(42,42,'approve:'+item['id'])
            submit.assert_not_called()
            self.control.prs(42,42)
            action=self.api.send.call_args.args[1]['inline_keyboard'][0][0]['callback_data']
            self.assertNotEqual(action,'approve:'+item['id'])

    def test_notification_suppresses_missing_acceptance_changed_head_and_api_failure(self):
        job=self.ready();c=self.control.coordinator;event='ready:'+str(job['id'])+':'+SHA
        with patch('kodi.coordinator.send') as send, \
                patch('kodi.github.pull',return_value=self.pr('b'*40)) as pull, \
                patch.object(c,'checks_pass',return_value=True):
            c.notification(event,c.ready_text(job));send.assert_not_called()
            pull.side_effect=RuntimeError('unavailable')
            with self.assertRaises(RuntimeError):c.notification(event,c.ready_text(job))
            send.assert_not_called()
            pull.reset_mock()
            with patch.object(c.store,'gate',return_value=False):
                c.notification(event,c.ready_text(job))
            pull.assert_not_called();send.assert_not_called()

    def test_uncertain_approval_is_not_replayed_and_forwarded_callback_rejected(self):
        item=approval_item(self.config,self.control.state,self.ready(),42)
        action='approve:'+item['id']
        update={'callback_query':{'id':'c','from':{'id':42},'data':action,
            'message':{'chat':{'id':42,'type':'private'},'forward_origin':{'type':'user'}}}}
        with patch('kodi.github.pull',return_value=self.pr()), \
                patch.object(self.control.coordinator,'checks_pass',return_value=True), \
                patch('kodi.github.approved_at',return_value=False), \
                patch('kodi.owner_review.approve',side_effect=RuntimeError('unknown result')) as submit:
            self.control.handle(update);submit.assert_not_called()
            with self.assertRaises(RuntimeError):self.control.callback(42,42,action)
            with self.assertRaisesRegex(ValueError,'could not be confirmed'):self.control.callback(42,42,action)
            submit.assert_called_once()

    def test_poll_skips_pre_feature_history_and_persists_offset(self):
        old={'update_id':9,'message':{'text':'/approve project 42'}};new=self.message('/help');new['update_id']=10
        self.api.call.side_effect=[[old],[new],[]]
        with patch.object(self.control,'handle') as handle:
            self.control.poll();self.control.poll()
        handle.assert_called_once_with(new)
        self.assertEqual(self.control.state.setting('offset'),'11')
        self.assertEqual(self.api.call.call_args.args[1]['offset'],11)

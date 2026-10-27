import json
import unittest
from unittest.mock import patch

from tests import test_telegram_control as controls


class ClarificationReplyTests(unittest.TestCase):
    message=controls.ControlTests.message

    def setUp(self):
        controls.ControlTests.setUp(self)
        self.config['clarification']={'enabled':True,'model':'gpt-6-luna','effort':'low'}
        store=self.control.coordinator.store
        self.job=store.enqueue('issue','project','Search','Fix search','board-1')
        store.update(self.job['id'],state='needs_input')
        with store.db:
            store.db.execute("INSERT INTO clarifications(job,revision,input_text,input_hash,input_description,state,result,telegram_chat,telegram_message) "
                "VALUES (?,1,?,?,?,'waiting',?,42,100)",(self.job['id'],'Fix search','hash','Fix search',json.dumps({'question':'What is wrong?'})))

    def test_direct_reply_is_attached_to_existing_task_not_a_new_draft_or_command(self):
        with patch.object(self.control,'draft') as draft,patch.object(self.control,'approve') as approve:
            self.control.handle(self.message('/approve project 42',reply_to_message={'message_id':100}))
        draft.assert_not_called();approve.assert_not_called()
        row=self.control.coordinator.store.db.execute('SELECT * FROM clarifications').fetchone()
        self.assertEqual(row['answer'],'/approve project 42')
        self.assertEqual(self.control.coordinator.store.get(self.job['id'])['state'],'needs_input')

    def test_replayed_or_superseded_reply_does_not_replace_answer(self):
        self.control.handle(self.message('Ignore case',reply_to_message={'message_id':100}))
        self.control.handle(self.message('Different answer',reply_to_message={'message_id':100}))
        row=self.control.coordinator.store.db.execute('SELECT * FROM clarifications').fetchone()
        self.assertEqual(row['answer'],'Ignore case')

    def test_unauthorized_or_forwarded_reply_cannot_answer(self):
        for extra in ({'from':{'id':99}}, {'forward_origin':{'type':'user'}},
                      {'chat':{'id':99,'type':'private'}}):
            self.control.handle(self.message('Answer',reply_to_message={'message_id':100},**extra))
        row=self.control.coordinator.store.db.execute('SELECT * FROM clarifications').fetchone()
        self.assertIsNone(row['answer'])

    def test_unrelated_message_keeps_existing_draft_confirmation(self):
        self.control.handle(self.message('project: Another task'))
        row=self.control.state.db.execute("SELECT * FROM items WHERE kind='ticket'").fetchone()
        self.assertEqual(row['state'],'pending')

    def test_reply_after_lost_send_response_uses_durable_question_reference(self):
        store=self.control.coordinator.store
        with store.db:
            store.db.execute('UPDATE clarifications SET telegram_message=NULL,question_token=?',('a'*32,))
        self.control.handle(self.message('Ignore case',reply_to_message={
            'message_id':999,'text':'[Kodi input: '+('a'*32)+']\nWhat is wrong?'}))
        row=store.db.execute('SELECT * FROM clarifications').fetchone()
        self.assertEqual(row['answer'],'Ignore case')
        self.assertEqual(self.control.state.db.execute('SELECT count(*) FROM items').fetchone()[0],0)


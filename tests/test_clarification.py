import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator


class ClarificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {'state_dir': self.temp.name, 'repos': {'project': {
            'github_repo': 'example/project', 'source': self.temp.name}},
            'board': {'steps': {'Ready': 'ready', 'Needs input': 'input'}},
            'clarification': {'enabled': True, 'model': 'gpt-6-luna', 'effort': 'low'}}
        self.c = Coordinator(self.config)
        self.addCleanup(lambda: self.c.store.db.close())
        self.c.board = Mock()
        self.task = {'id': 'task-1', 'title': 'Fix it', 'description': 'Repo: project\n\nFix search',
                     'workflow_step_id': 'ready'}
        self.c.board.tasks.return_value = [self.task]
        self.git = patch('kodi.clarification.git', return_value='a' * 40)
        self.git.start()
        self.addCleanup(self.git.stop)
        self.worker = patch('kodi.clarification.run_clarifier')
        self.run = self.worker.start()
        self.addCleanup(self.worker.stop)
        self.run.return_value = {'status': 'needs_input', 'title': 'Fix search',
                                 'description': 'Fix search.', 'question': 'What search behavior is wrong?'}

    def sync(self):
        with patch('kodi.github.authenticated', return_value=True), \
                patch('kodi.github.issues', return_value=[]), \
                patch('kodi.github.create_issue', return_value='https://github.com/example/project/issues/1'):
            self.c.sync()
        return self.c.store.db.execute('SELECT * FROM jobs').fetchone()

    def test_missing_essentials_block_same_task_before_coding(self):
        job = self.sync()
        self.assertEqual(job['state'], 'needs_input')
        self.assertEqual(job['attempt'], 0)
        self.assertIsNone(self.c.store.claim())
        self.c.board.move.assert_called_with(self.config['board'], 'task-1', 'Needs input')
        text = self.c.board.description.call_args.args[1]
        self.assertIn('Fix search', text)
        self.assertIn('What search behavior is wrong?', text)

    def test_delivery_scope_is_frozen_before_intake_and_survives_answer(self):
        def needs_input(*args):
            pending = self.c.store.db.execute('SELECT * FROM jobs').fetchone()
            self.assertEqual(pending['state'], 'intake_pending')
            contract = self.c.store.delivery_contract(pending['id'])
            self.assertEqual(contract['task_type'], 'IMPLEMENTATION')
            self.assertEqual(contract['dispatch_stage'], 'Ready')
            self.assertIsNone(self.c.store.claim())
            return {'status': 'needs_input', 'title': 'Fix search',
                    'description': 'Fix search.', 'question': 'Which matching behavior?'}

        self.run.side_effect = needs_input
        job = self.sync()
        original = self.c.store.delivery_contract(job['id'])
        self.task['description'] = self.c.board.description.call_args.args[1] + '\nAnswer: Ignore case.'
        self.run.side_effect = None
        self.run.return_value = {'status': 'actionable', 'title': 'Fix search',
                                'description': 'Match case-insensitively.', 'question': ''}
        self.sync()
        self.assertEqual(self.c.store.get(job['id'])['state'], 'queued')
        self.assertEqual(self.c.store.delivery_contract(job['id']), original)
        self.assertEqual(original['requirements'], 'Repo: project\n\nFix search')

    def test_actionable_request_queues_without_questions_and_preserves_original(self):
        self.run.return_value = {'status': 'actionable', 'title': 'Fix search case matching',
                                 'description': 'Make search case-insensitive. Verify mixed-case matches.', 'question': ''}
        job = self.sync()
        self.assertEqual(job['state'], 'queued')
        self.assertIn('Fix search', job['requirements'])
        self.assertIn('case-insensitive', job['requirements'])
        self.c.board.move.assert_not_called()
        self.sync()
        self.assertEqual(self.run.call_count, 1)

    def test_ready_without_answer_does_not_repeat_model_or_start_coding(self):
        job = self.sync()
        self.task['description'] = self.c.board.description.call_args.args[1]
        self.sync()
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(self.c.store.get(job['id'])['state'], 'needs_input')

    def test_description_answer_then_ready_rechecks_same_job_after_restart(self):
        job = self.sync()
        self.task['description'] = self.c.board.description.call_args.args[1] + '\n\nAnswer: Search should ignore case.'
        self.c.store.db.close()
        self.c = Coordinator(self.config)
        self.c.board = Mock()
        self.c.board.tasks.return_value = [self.task]
        self.run.return_value = {'status': 'actionable', 'title': 'Fix search case matching',
                                 'description': 'Ignore case in search; verify mixed-case matches.', 'question': ''}
        self.sync()
        current = self.c.store.get(job['id'])
        self.assertEqual(current['state'], 'queued')
        self.assertEqual(current['board_id'], 'task-1')
        self.assertIn('Search should ignore case', current['requirements'])
        self.assertIn('What search behavior is wrong?', current['requirements'])
        self.assertEqual(self.c.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_backlog_is_not_submitted_and_existing_blocked_jobs_are_not_resumed(self):
        self.task['workflow_step_id'] = 'backlog'
        self.sync()
        self.run.assert_not_called()
        job = self.c.store.enqueue('legacy', 'project', 'Existing', 'Existing', 'task-1')
        self.c.store.update(job['id'], state='blocked', attempt=2)
        self.task['workflow_step_id'] = 'ready'
        self.sync()
        self.run.assert_not_called()
        self.assertEqual(self.c.store.get(job['id'])['state'], 'blocked')

    def test_invalid_output_and_provider_failure_never_start_coding(self):
        for value in ({'status': 'actionable', 'title': 'Invented'},
                      {'status': 'actionable', 'title': 'Fine', 'description': 'Fine',
                       'question': '', 'grant_permission': True}):
            with self.subTest(value=value):
                self.run.return_value = value
                job = self.sync()
                self.assertEqual(job['state'], 'blocked')
                self.assertIsNone(self.c.store.claim())

    def test_provider_failure_is_blocked_without_leaking_exception(self):
        self.run.side_effect=RuntimeError('sensitive provider details')
        job=self.sync()
        self.assertEqual(job['state'],'blocked')
        self.assertNotIn('sensitive',job['error'])
        self.assertEqual(job['attempt'],0)

    def test_telegram_answer_rechecks_and_repeated_missing_answer_asks_on_same_task(self):
        from kodi.clarification import Intake
        job=self.sync()
        self.task['description']=self.c.board.description.call_args.args[1]
        self.task['workflow_step_id']='input'
        self.assertTrue(Intake(self.c).answer(job['id'],1,'The case matching is wrong','telegram:42:1'))
        self.sync()
        self.assertEqual(self.run.call_count,2)
        self.assertEqual(self.c.store.get(job['id'])['state'],'needs_input')
        self.assertEqual(Intake(self.c).latest(job['id'])['revision'],2)
        self.assertEqual(self.c.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)

    def test_completed_decision_is_reprojected_after_board_failure_without_model_rerun(self):
        self.c.board.description.side_effect=RuntimeError('board unavailable')
        with self.assertRaises(RuntimeError):self.sync()
        self.c.board.description.side_effect=None
        self.sync()
        self.assertEqual(self.run.call_count,1)
        self.assertEqual(self.c.store.db.execute('SELECT state FROM jobs').fetchone()[0],'needs_input')

    def test_cancelled_job_is_not_resumed_by_a_saved_answer(self):
        from kodi.clarification import Intake
        job=self.sync()
        Intake(self.c).answer(job['id'],1,'Answer','telegram:42:1')
        self.c.store.update(job['id'],state='cancelled')
        self.sync()
        self.assertEqual(self.run.call_count,1)
        self.assertEqual(self.c.store.get(job['id'])['state'],'cancelled')

    def test_disabled_mode_preserves_existing_dispatch(self):
        self.config['clarification']['enabled']=False
        job=self.sync()
        self.assertEqual(job['state'],'queued')
        self.run.assert_not_called()

    def test_setup_error_prevents_dispatch_without_needs_input_column(self):
        del self.config['board']['steps']['Needs input']
        with self.assertRaisesRegex(ValueError,'Needs input'):self.sync()
        self.run.assert_not_called()

    def test_crash_between_enqueue_and_first_round_remains_unclaimable_and_recovers(self):
        job=self.c.store.enqueue('issue','project','Search','Fix search','task-1',initial_state='intake_pending')
        self.assertIsNone(self.c.store.claim())
        self.sync()
        self.assertEqual(self.c.store.get(job['id'])['state'],'needs_input')
        self.assertEqual(self.run.call_count,1)

    def test_uncertain_question_delivery_is_not_repeated_and_board_remains_usable(self):
        self.config.update(telegram_env='fixture',telegram={'enabled':True})
        api=Mock();api.chat_id=42
        api.send.side_effect=[RuntimeError('offline'),123]
        with patch('kodi.clarification.TelegramAPI',return_value=api):
            job=self.sync()
            self.task['workflow_step_id']='input'
            self.sync()
            self.sync()
        row=self.c.store.db.execute('SELECT * FROM clarifications').fetchone()
        self.assertIsNone(row['telegram_message'])
        self.assertIsNotNone(row['question_token'])
        self.assertIn('KanDev',self.c.store.get(job['id'])['error'])
        self.assertEqual(api.send.call_count,1)
        self.assertEqual(self.run.call_count,1)

    def test_selected_repository_is_attached_to_issue_mirror_when_configured(self):
        self.config['board']['repository_ids']={'project':'native-repo'}
        self.c.board.tasks.return_value=[]
        self.c.board.create.return_value={'id':'mirror-1'}
        with patch('kodi.github.authenticated',return_value=True),patch('kodi.github.issues',return_value=[{
                'url':'https://github.com/example/project/issues/1','title':'Search','body':'Fix search'}]):
            self.c.sync()
        self.c.board.create.assert_called_once_with(self.config['board'],'Search',
            'Repo: project\n\nFix search\n\nGitHub issue: https://github.com/example/project/issues/1',
            'https://github.com/example/project/issues/1',repository_id='native-repo')

    def test_edit_during_model_is_preserved_and_rechecked_before_coding(self):
        self.c.board.request.return_value={**self.task,'description':'Repo: project\n\nSearch should ignore case'}
        job=self.sync()
        self.assertEqual(job['state'],'intake_pending')
        self.assertIsNone(self.c.store.claim())
        self.c.board.description.assert_not_called()
        self.assertIn('Search should ignore case',self.c.store.db.execute(
            'SELECT input_text FROM clarifications ORDER BY revision DESC LIMIT 1').fetchone()[0])

    def test_returning_to_backlog_during_model_does_not_queue_or_overwrite_task(self):
        self.c.board.request.return_value={**self.task,'workflow_step_id':'backlog'}
        job=self.sync()
        self.assertEqual(job['state'],'intake_pending')
        self.assertIsNone(self.c.store.claim())
        self.c.board.description.assert_not_called()

    def test_title_edit_during_model_is_not_overwritten(self):
        self.c.board.request.return_value={**self.task,'title':'Search should ignore case'}
        job=self.sync()
        self.assertEqual(job['state'],'intake_pending')
        self.c.board.description.assert_not_called()
        self.assertIn('Search should ignore case',self.c.store.db.execute(
            'SELECT input_text FROM clarifications ORDER BY revision DESC LIMIT 1').fetchone()[0])

    def test_answers_from_both_channels_are_preserved(self):
        from kodi.clarification import Intake
        job=self.sync()
        self.task['description']=self.c.board.description.call_args.args[1]+'\n\nKeep punctuation unchanged.'
        self.task['workflow_step_id']='input'
        Intake(self.c).answer(job['id'],1,'Ignore case.','telegram:42:1')
        self.sync()
        text=Intake(self.c).latest(job['id'])['input_text']
        self.assertIn('Ignore case.',text)
        self.assertIn('Keep punctuation unchanged.',text)


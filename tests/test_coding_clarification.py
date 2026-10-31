"""Synthetic delivery tests: external workers, board and Git are simulated."""
import fcntl
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from tests import test_clarification as fixtures
from kodi.clarification import Intake, validate_coding_question
from kodi.coordinator import Coordinator
from kodi.failures import DeliveryFailure


class CodingClarificationTests(unittest.TestCase):
    setUp = fixtures.ClarificationTests.setUp
    sync = fixtures.ClarificationTests.sync
    question = dict(status='needs_input', category='missing_business_requirement',
                    question='Should matching ignore case?',
                    context='The requested matching rule does not specify case handling.')

    def prepare(self):
        self.config['board']['steps'].update(Coding='coding', QA='qa', Review='review', Blocked='blocked')
        self.config['max_attempts'] = 3
        self.config['repos']['project']['qa_suite']='fixture'
        self.c.board.description.side_effect = lambda task, text: self.task.update(description=text)
        self.c.board.move.side_effect = lambda config, task, step: self.task.update(
            workflow_step_id=config['steps'][step])
        def request(path, data=None, **kwargs):
            if data:self.task.update(data)
            return dict(self.task)
        self.c.board.request.side_effect = request
        self.run.return_value = dict(status='actionable', title='Fix search', description='Fix matching.', question='')
        job = self.sync()
        self.assertEqual(job['state'], 'queued')
        self.checkout = Path(self.temp.name)/'jobs'/str(job['id'])/'checkout'
        self.checkout.mkdir(parents=True,exist_ok=True)
        self.dirty = self.checkout/'search.py'
        self.dirty.write_text('partial implementation\n')
        self.c.store.update(job['id'], state='coding', attempt=1)
        self.c.store.reserve_recovery(job['id'], 'example-recovery', 'original', 2)
        return self.c.store.get(job['id'])

    def ask(self, job):
        self.run.return_value = dict(status='needs_input', title='Fix search',
                                    description=self.question['context'], question=self.question['question'])
        Intake(self.c).coding_question(job, dict(self.question), feedback='Retain earlier review findings')
        self.assertEqual(self.c.store.get(job['id'])['state'], 'needs_input')
        return Intake(self.c).latest(job['id'])

    def answer(self, job, row, channel='board'):
        if channel=='board':
            self.task['description'] += '\nAnswer: Ignore case.'
            self.task['workflow_step_id'] = 'ready'
        else:
            self.assertTrue(Intake(self.c).answer(job['id'], row['revision'], 'Ignore case.', 'telegram:42:22'))
        self.run.return_value = dict(status='actionable', title='Fix search', description='Ignore case.', question='')
        self.sync()
        return self.c.store.get(job['id'])

    def test_board_answer_preserves_identity_dirty_work_budgets_and_history(self):
        self.check_answer_channel('board')

    def test_telegram_answer_preserves_identity_dirty_work_budgets_and_history(self):
        self.check_answer_channel('telegram')

    def check_answer_channel(self,channel):
        job=self.prepare()
        self.config.update(telegram_env='fixture', telegram={'enabled':True})
        api=Mock(chat_id=42);api.send.return_value=100
        with patch('kodi.clarification.TelegramAPI', return_value=api):row=self.ask(job)
        self.assertEqual(row['telegram_message'],100)
        self.assertIn(self.question['question'],api.send.call_args.args[0])
        self.assertEqual(self.task['workflow_step_id'],'input')
        current=self.answer(job,row,channel)
        self.assertEqual(current['state'],'queued')
        self.assertEqual(current['attempt'],1)
        self.assertEqual(current['board_id'],job['board_id'])
        self.assertIn('Original request:',current['requirements'])
        self.assertIn(self.question['question'],current['requirements'])
        self.assertIn('Ignore case.',current['requirements'])
        self.assertEqual(self.dirty.read_text(),'partial implementation\n')
        self.assertEqual(self.c.store.db.execute('SELECT attempts FROM recovery_attempts').fetchone()[0],1)
        plan=Intake(self.c).continuation(current)
        self.assertEqual(plan['stage'],'coding')
        self.assertEqual(plan['feedback'],'Retain earlier review findings')
        self.assertFalse(self.c.store.gate(job['id'],'a'*40))

    def test_partial_answer_adds_round_and_duplicate_event_cannot_replace_it(self):
        job=self.prepare();row=self.ask(job)
        intake=Intake(self.c)
        self.assertTrue(intake.answer(job['id'],row['revision'],'Unsure','telegram:42:1'))
        self.assertFalse(intake.answer(job['id'],row['revision'],'Replacement','telegram:42:1'))
        self.sync()  # intake still requires the rule
        latest=intake.latest(job['id'])
        self.assertEqual(latest['revision'],row['revision']+1)
        self.assertIn('Unsure',latest['input_text'])
        self.assertEqual(self.c.store.get(job['id'])['attempt'],1)
        self.answer(job,latest)
        self.assertIn('Unsure',self.c.store.get(job['id'])['requirements'])

    def test_restart_reconciles_uncertain_board_projection_without_repeating_intake(self):
        job=self.prepare()
        original=self.c.board.description.side_effect
        self.c.board.description.side_effect=RuntimeError('lost response')
        self.run.return_value=dict(status='needs_input',title='Fix search',description='Missing case rule',question=self.question['question'])
        Intake(self.c).coding_question(job,dict(self.question))
        self.assertEqual(self.c.store.get(job['id'])['state'],'intake_pending')
        calls=self.run.call_count
        board=self.c.board
        self.c.store.db.close();self.c=Coordinator(self.config);self.c.board=board
        self.c.board.description.side_effect=original
        self.sync()
        self.assertEqual(self.run.call_count,calls)
        self.assertEqual(self.c.store.get(job['id'])['state'],'needs_input')
        self.assertEqual(self.dirty.read_text(),'partial implementation\n')

    def test_changed_candidate_requirements_and_exhausted_attempts_refuse_continuation(self):
        job=self.prepare();row=self.ask(job);current=self.answer(job,row)
        intake=Intake(self.c)
        with patch.object(intake,'checkout_identity',return_value={}):
            with self.assertRaisesRegex(ValueError,'identity changed'):intake.continuation(current)
        self.c.store.update(job['id'],requirements='Different work')
        with self.assertRaisesRegex(ValueError,'identity changed'):intake.continuation(self.c.store.get(job['id']))
        self.c.store.update(job['id'],requirements=current['requirements'])
        self.config['max_attempts']=1
        with self.assertRaisesRegex(DeliveryFailure,'budget exhausted'):intake.continuation(current)
        self.assertEqual(self.dirty.read_text(),'partial implementation\n')

    def test_pause_cancel_and_defer_do_not_consume_saved_answers(self):
        job=self.prepare();row=self.ask(job)
        Intake(self.c).answer(job['id'],row['revision'],'Ignore case.','telegram:42:1')
        calls=self.run.call_count
        for state,step in (('paused','input'),('cancelled','input'),('needs_input','backlog')):
            self.c.store.update(job['id'],state=state);self.task['workflow_step_id']=step
            self.sync()
            self.assertEqual(self.c.store.get(job['id'])['state'],state)
            self.assertEqual(Intake(self.c).latest(job['id'])['state'],'waiting')
        self.assertEqual(self.run.call_count,calls)

    def test_concurrent_sync_and_uncertain_executor_do_not_start_intake(self):
        job=self.prepare();row=self.ask(job)
        Intake(self.c).answer(job['id'],row['revision'],'Ignore case.','telegram:42:1')
        calls=self.run.call_count
        path=Path(self.temp.name)/('clarification-'+str(job['id'])+'.lock')
        with path.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.sync()
        token=self.c.store.begin_invocation(job['id'],'coding')
        self.assertIsNotNone(token)
        self.sync()
        self.assertEqual(self.run.call_count,calls)
        self.assertIsNone(self.c.store.claim())

    def test_strict_schema_and_independent_intake_reject_errors_and_permission_requests(self):
        for value in ({**self.question,'category':'code_defect'}, {**self.question,'extra':True},
                      {**self.question,'question':'May I bypass permissions?'},
                      {**self.question,'context':'Need credentials'}, {**self.question,'question':''}):
            with self.subTest(value=value),self.assertRaises((ValueError,DeliveryFailure)):
                validate_coding_question(value)
        job=self.prepare()
        self.run.return_value=dict(status='actionable',title='Fix search',description='Resolve code defect.',question='')
        with self.assertRaisesRegex(ValueError,'not confirmed'):Intake(self.c).coding_question(job,dict(self.question))
        self.assertEqual(self.c.store.get(job['id'])['state'],'coding')
        self.run.side_effect=RuntimeError('provider failure')
        with self.assertRaises(DeliveryFailure) as error:Intake(self.c).coding_question(job,dict(self.question))
        self.assertEqual(error.exception.signal,'clarification_unavailable')

    def test_round_limit_is_explicit_and_never_resets_attempts(self):
        job=self.prepare();row=self.ask(job)
        with self.c.store.db:self.c.store.db.execute('UPDATE clarifications SET revision=8 WHERE job=? AND revision=?',(job['id'],row['revision']))
        Intake(self.c).answer(job['id'],8,'Still unsure','telegram:42:1')
        self.sync()
        current=self.c.store.get(job['id'])
        self.assertEqual(current['state'],'blocked')
        self.assertIn('round limit',current['error'])
        self.assertEqual(current['attempt'],1)

    def test_resumed_process_uses_preserved_checkout_and_runs_normal_qa_review(self):
        job=self.prepare();row=self.ask(job);current=self.answer(job,row)
        author=dict(summary='Implemented case handling', test_steps=['Search mixed-case text'],
                    delivery_status='implemented',behavioral_evidence=['Mixed-case regression passes'])
        def worker(*args,**kwargs):
            self.assertEqual(self.dirty.read_text(),'partial implementation\n')
            self.assertIn('Ignore case.',args[3])
            self.assertIn('Retain earlier review findings',args[3])
            return author,{},0
        real_exists=Path.exists
        def exists(path):return True if path.name=='.git' else real_exists(path)
        def git(checkout,*args):
            if args[:3]==('diff','--cached','--name-only'):return 'search.py\0'
            return 'a'*40
        self.c.store.claim()
        with ExitStack() as stack:
            stack.enter_context(patch('kodi.coordinator.Path.exists',exists))
            stack.enter_context(patch('kodi.coordinator.git',side_effect=git))
            stack.enter_context(patch('kodi.coordinator.run_worker',side_effect=worker))
            clone=stack.enter_context(patch('kodi.coordinator.subprocess.run'))
            refresh=stack.enter_context(patch.object(self.c,'refresh_source'))
            qa=stack.enter_context(patch('kodi.coordinator.qa',return_value={'passed':True}))
            review=stack.enter_context(patch.object(self.c,'review',return_value={'approved':True}))
            stack.enter_context(patch.object(self.c.store,'gate',return_value=True))
            stack.enter_context(patch.object(self.c,'publish'))
            stack.enter_context(patch.object(self.c,'progress'))
            self.c.process(self.c.store.get(job['id']))
        clone.assert_not_called();refresh.assert_not_called()
        qa.assert_called_once();review.assert_called_once()
        self.assertEqual(self.c.store.get(job['id'])['attempt'],2)
        self.assertEqual(self.c.store.get(job['id'])['state'],'validated')

    def test_coding_worker_outcome_routes_before_report_repair_or_qa(self):
        job=self.prepare()
        self.c.store.update(job['id'],state='coding',attempt=0)
        self.run.return_value=dict(status='needs_input',title='Fix search',
                                   description=self.question['context'],question=self.question['question'])
        real_exists=Path.exists
        def exists(path):
            if path.name=='.git':return True
            if path==self.checkout:return False
            return real_exists(path)
        with ExitStack() as stack:
            stack.enter_context(patch('kodi.coordinator.Path.exists',exists))
            stack.enter_context(patch('kodi.coordinator.git',return_value='a'*40))
            stack.enter_context(patch('kodi.coordinator.subprocess.run'))
            stack.enter_context(patch.object(self.c,'refresh_source'))
            worker=stack.enter_context(patch('kodi.coordinator.run_worker',return_value=(dict(self.question),{},0)))
            repair=stack.enter_context(patch('kodi.coordinator.reconcile_report'))
            qa=stack.enter_context(patch('kodi.coordinator.qa'))
            self.c.process(self.c.store.get(job['id']))
        worker.assert_called_once();repair.assert_not_called();qa.assert_not_called()
        self.assertEqual(self.c.store.get(job['id'])['state'],'needs_input')
        self.assertEqual(self.task['workflow_step_id'],'input')
        self.assertIn(self.question['question'],self.task['description'])
        self.assertEqual(self.c.store.get(job['id'])['attempt'],1)

    def test_notification_outage_allows_board_answer_without_resending(self):
        job=self.prepare()
        self.config.update(telegram_env='fixture',telegram={'enabled':True})
        api=Mock(chat_id=42);api.send.side_effect=RuntimeError('response unavailable')
        with patch('kodi.clarification.TelegramAPI',return_value=api):
            row=self.ask(job)
            self.sync()
            current=self.answer(job,row)
        self.assertEqual(api.send.call_count,1)
        self.assertEqual(current['state'],'queued')
        self.assertIsNotNone(Intake(self.c).continuation(current))

    def test_remote_uncertainty_and_live_ready_changes_prevent_worker_continuation(self):
        job=self.prepare();row=self.ask(job);current=self.answer(job,row)
        for step in ('backlog','paused','cancelled'):
            self.task['workflow_step_id']=step
            with self.assertRaisesRegex(ValueError,'no longer Ready'):Intake(self.c).continuation(current)
        self.task['workflow_step_id']='ready'
        self.c.store.evidence(job['id'],'','merge_uncertain',{'reason':'unknown result'})
        with self.assertRaisesRegex(ValueError,'Uncertain remote mutation'):Intake(self.c).continuation(current)
        self.assertEqual(self.dirty.read_text(),'partial implementation\n')

    def test_changed_requirements_invalidate_previously_passing_acceptance(self):
        job=self.prepare();sha='a'*40
        author=dict(summary='Earlier candidate',test_steps=['Search'],delivery_status='implemented',
                    behavioral_evidence=['Earlier matching checks'])
        self.c.store.evidence(job['id'],sha,'author',author)
        self.c.store.evidence(job['id'],sha,'qa',{'passed':True})
        self.c.store.evidence(job['id'],sha,'review',dict(approved=True,criteria_met=True,simple=True,
            delivery_met=True,author_family='openai',reviewer_family='google',blocking_findings=[]))
        self.assertTrue(self.c.store.gate(job['id'],sha))
        row=self.ask(job);self.answer(job,row)
        self.assertFalse(self.c.store.gate(job['id'],sha))
        self.assertEqual(self.c.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind IN ('author','qa','review')").fetchone()[0],3)

    def test_unavailable_recheck_retains_answer_history_on_board(self):
        job=self.prepare();row=self.ask(job)
        Intake(self.c).answer(job['id'],row['revision'],'Ignore case.','telegram:42:1')
        self.run.side_effect=RuntimeError('intake offline')
        self.sync()
        self.assertEqual(self.c.store.get(job['id'])['state'],'blocked')
        self.assertIn('unavailable',self.task['description'])
        self.assertIn(self.question['question'],self.task['description'])
        self.assertIn('Ignore case.',self.task['description'])
        self.assertEqual(self.dirty.read_text(),'partial implementation\n')

    def test_defer_during_question_projection_prevents_move_and_notification(self):
        job=self.prepare()
        self.config.update(telegram_env='fixture',telegram={'enabled':True})
        self.run.return_value=dict(status='needs_input',title='Fix search',description='Missing rule',question=self.question['question'])
        def defer(task,text):self.task.update(description=text,workflow_step_id='backlog')
        self.c.board.description.side_effect=defer
        self.c.board.move.reset_mock()
        with patch('kodi.clarification.TelegramAPI') as api:
            Intake(self.c).coding_question(job,dict(self.question))
        self.c.board.move.assert_not_called();api.assert_not_called()
        self.assertEqual(self.c.store.get(job['id'])['state'],'intake_pending')
        self.assertIn(self.question['question'],Intake(self.c).latest(job['id'])['result'])

    def test_cancel_during_failed_intake_does_not_overwrite_control_state(self):
        job=self.prepare();row=self.ask(job)
        Intake(self.c).answer(job['id'],row['revision'],'Ignore case.','telegram:42:1')
        def cancel(*args):
            self.c.store.update(job['id'],state='cancelled')
            raise RuntimeError('intake unavailable')
        self.run.side_effect=cancel
        self.sync()
        self.assertEqual(self.c.store.get(job['id'])['state'],'cancelled')
        self.assertEqual(self.dirty.read_text(),'partial implementation\n')

    def test_missing_continuation_checkout_never_clones_or_resets_attempts(self):
        job=self.prepare();row=self.ask(job);self.answer(job,row)
        with patch('kodi.coordinator.Path.exists',side_effect=[True,False]), \
                patch.object(self.c,'refresh_source') as refresh, \
                patch('kodi.coordinator.subprocess.run') as clone, \
                patch('kodi.coordinator.run_worker') as worker, \
                patch.object(self.c,'progress'):
            self.c.process(self.c.store.get(job['id']))
        refresh.assert_not_called();clone.assert_not_called();worker.assert_not_called()
        self.assertEqual(self.c.store.get(job['id'])['attempt'],1)
        self.assertEqual(self.c.store.get(job['id'])['state'],'blocked')

import tempfile
import unittest
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator


class BackgroundTrackingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = Coordinator({'state_dir': directory.name,
            'repos': {'project': {'github_repo': 'example/project'}},
            'board': {'steps': {s: s for s in
                ('Backlog', 'Ready', 'Review', 'Human review', 'Blocked', 'Done')}}})
        self.addCleanup(self.c.store.db.close)
        self.tasks = []
        self.c.board = Mock()
        self.c.board.tasks.side_effect = lambda settings: list(self.tasks)
        def create(settings, title, description, external_id):
            task = dict(id='tracking', title=title, description=description,
                        external_id=external_id, workflow_step_id='Backlog')
            self.tasks.append(task)
            return task
        self.c.board.create.side_effect = create
        def description(task_id, text):
            next(t for t in self.tasks if t['id'] == task_id)['description'] = text
        self.c.board.description.side_effect = description
        job = self.c.store.enqueue('learning:example', 'project',
                                   'Document review lessons', 'Example requirements')
        self.id = job['id']
        self.c.store.update(self.id, state='human_review', commit_sha='a'*40,
                            pr_url='https://github.com/example/project/pull/85')

    def test_legacy_pr_reconciles_once_and_exposes_owner_without_accepting(self):
        before = self.c.store.get(self.id)
        self.c.sync_background_prs()
        self.c.sync_background_prs()
        self.c.board.create.assert_called_once()
        text = self.tasks[0]['description']
        self.assertIn(before['pr_url'], text)
        self.assertIn('Kodi status: human_review', text)
        self.assertIn('Repository queue: retains coding ownership', text)
        self.c.board.move.assert_called_with(self.c.config['board'], 'tracking', 'Human review')
        current = self.c.store.get(self.id)
        for field in ('state', 'commit_sha', 'attempt', 'requirements'):
            self.assertEqual(current[field], before[field])
        waiting = self.c.store.enqueue('issue:next', 'project', 'Next', 'Next task')
        self.assertEqual(self.c.store.repository_blocker(waiting)['id'], self.id)
        self.tasks.append(dict(id='waiting', description='Next task'))
        self.c.store.update(waiting['id'], board_id='waiting')
        self.c.progress(self.c.store.get(waiting['id']))
        self.assertIn('Blocking PR: '+before['pr_url'], self.tasks[1]['description'])
        self.assertIn('Blocking Kandev task: tracking', self.tasks[1]['description'])

    def test_create_response_lost_is_reconciled_by_stable_identity(self):
        create = self.c.board.create.side_effect
        def lost(*args):
            create(*args)
            raise TimeoutError('response lost')
        self.c.board.create.side_effect = lost
        self.c.sync_background_prs()
        self.assertIsNone(self.c.store.get(self.id)['board_id'])
        restarted = Coordinator(self.c.config)
        self.addCleanup(restarted.store.db.close)
        restarted.board = self.c.board
        self.c = restarted
        self.c.sync_background_prs()
        self.assertEqual(len(self.tasks), 1)
        self.assertEqual(self.c.store.get(self.id)['board_id'], 'tracking')

    def test_ambiguous_or_changed_tracking_fails_without_linking(self):
        self.c.sync_background_prs()
        self.c.store.update(self.id, board_id=None)
        original = dict(self.tasks[0])
        self.tasks.append(dict(original, id='duplicate'))
        self.c.sync_background_prs()
        self.tasks.pop()
        self.tasks[0]['description'] = 'Changed requirements'
        self.c.sync_background_prs()
        self.assertIsNone(self.c.store.get(self.id)['board_id'])
        self.assertEqual(self.c.store.get(self.id)['state'], 'human_review')

    def test_trailing_whitespace_reconciles_but_substantive_drift_blocks(self):
        from kodi.learning import tracking_requirements
        job = self.c.store.get(self.id)
        self.tasks.append(dict(id='tracking', title=job['title'],
            description=tracking_requirements(job)+'  \n',
            external_id='kodi-internal:'+str(self.id), workflow_step_id='Backlog'))
        self.c.sync_background_prs()
        self.c.board.create.assert_not_called()
        self.assertEqual(self.c.store.get(self.id)['board_id'], 'tracking')
        self.tasks[0]['description'] = 'Substantively changed'
        self.assertFalse(self.c.progress(self.c.store.get(self.id)))
        self.c.sync_background_prs()
        self.assertEqual(self.tasks[0]['description'], 'Substantively changed')
        self.assertFalse(self.c.store.gate(self.id, 'a'*40))

    def test_failed_job_does_not_stop_other_tracking_or_delivery_stages(self):
        other = self.c.store.enqueue('internal:other', 'project', 'Other', 'Other requirements')
        self.c.store.update(other['id'], state='ci_pending', pr_url='https://github.com/example/project/pull/86')
        create = self.c.board.create.side_effect
        def fail_first(settings, title, description, external_id):
            if external_id == 'kodi-internal:'+str(self.id):
                raise TimeoutError('private response must not be retained')
            return create(settings, title, description, external_id)
        self.c.board.create.side_effect = fail_first
        with patch.object(self.c, 'finish_prs'), patch.object(self.c, 'finish_checks') as checks, \
                patch.object(self.c, 'request_reviews') as reviews, \
                patch.object(self.c, 'maintain_merges') as merges, \
                patch('kodi.github.authenticated', return_value=False):
            self.c._tick()
        checks.assert_called_once()
        reviews.assert_called_once()
        merges.assert_called_once()
        self.assertEqual(self.c.store.get(other['id'])['board_id'], 'tracking')
        evidence = self.c.store.db.execute(
            "SELECT data FROM evidence WHERE job=? AND kind='tracking_error'", (self.id,)).fetchone()[0]
        self.assertIn('next_action', evidence)
        self.assertNotIn('private response', evidence)
        self.assertEqual(self.c.store.get(self.id)['state'], 'human_review')

    def test_description_and_move_failures_remain_retryable(self):
        self.c.board.description.side_effect = TimeoutError('unavailable')
        self.c.sync_background_prs()
        self.assertEqual(self.c.store.get(self.id)['board_id'], 'tracking')
        self.c.board.description.side_effect = None
        self.c.board.move.side_effect = TimeoutError('unavailable')
        self.c.sync_background_prs()
        self.c.board.move.side_effect = None
        self.c.sync_background_prs()
        self.c.board.create.assert_called_once()
        self.c.board.move.assert_called_with(self.c.config['board'], 'tracking', 'Human review')

    def test_merge_closure_and_cancellation_refresh_terminal_tracking(self):
        for remote, expected in (('MERGED', 'done'), ('CLOSED', 'cancelled'), (None, 'cancelled')):
            with self.subTest(remote=remote):
                self.c.store.update(self.id, state='human_review' if remote else 'cancelled')
                with patch('kodi.github.pr_state', return_value=remote):
                    self.c.finish_prs()
                self.c.sync_background_prs()
                self.assertIn('Kodi status: '+expected, self.tasks[0]['description'])
                self.c.board.move.assert_called_with(self.c.config['board'], 'tracking', 'Done')
                waiting = self.c.store.enqueue('next:'+str(remote), 'project', 'Next', 'Next')
                self.assertIsNone(self.c.store.repository_blocker(waiting))
        self.c.board.create.assert_called_once()

    def test_new_publication_progress_immediately_creates_tracking(self):
        self.c.progress(self.c.store.get(self.id))
        self.assertEqual(self.c.store.get(self.id)['board_id'], 'tracking')
        self.assertIn('/pull/85', self.tasks[0]['description'])

    def test_historical_board_idle_operations_and_correction_after_restart(self):
        # 199 retained jobs, 191 terminal; all tasks already linked.
        self.c.store.update(self.id, state='done')
        for number in range(199):
            if number == 0:
                job = self.c.store.get(self.id)
            else:
                job = self.c.store.enqueue('internal:'+str(number), 'project',
                    'Historical task', 'Example requirements')
                self.c.store.update(job['id'], state='done' if number < 191 else 'human_review',
                    pr_url='https://github.com/example/project/pull/'+str(number+100))
                job = self.c.store.get(job['id'])
            from kodi.learning import tracking_requirements
            task = dict(id='task-'+str(number), description=tracking_requirements(job),
                external_id='kodi-internal:'+str(job['id']), workflow_step_id='Backlog')
            self.tasks.append(task)
            self.c.store.update(job['id'], board_id=task['id'])
        def move(settings, task_id, step):
            next(t for t in self.tasks if t['id'] == task_id)['workflow_step_id'] = step
        self.c.board.move.side_effect = move
        self.c.sync_background_prs()
        self.c.board.reset_mock()
        self.c.sync_background_prs()
        self.c.sync_background_prs()
        self.assertEqual(self.c.board.tasks.call_count, 2)
        self.c.board.description.assert_not_called()
        self.c.board.move.assert_not_called()
        self.c.board.create.assert_not_called()
        restarted = Coordinator(self.c.config)
        self.addCleanup(restarted.store.db.close)
        restarted.board = self.c.board
        restarted.sync_background_prs()
        # Restart legitimately reports interrupted timing for the eight open jobs.
        self.assertEqual(self.c.board.description.call_count, 8)
        self.assertTrue(all(call.args[0] != 'task-0' for call in self.c.board.description.call_args_list))
        self.c.board.reset_mock()
        restarted.sync_background_prs()
        self.c.board.description.assert_not_called()
        restarted.store.update(self.id, error='Corrected terminal evidence')
        restarted.sync_background_prs()
        self.c.board.description.assert_called_once()
        self.assertIn('Corrected terminal evidence', self.tasks[0]['description'])

    def test_failed_description_retries_and_user_edit_is_preserved(self):
        self.c.sync_background_prs()
        self.c.store.update(self.id, error='New evidence')
        update = self.c.board.description.side_effect
        self.c.board.description.side_effect = TimeoutError('unavailable')
        self.c.sync_background_prs()
        self.assertNotIn('New evidence', self.tasks[0]['description'])
        self.c.board.description.side_effect = update
        self.c.sync_background_prs()
        self.assertIn('New evidence', self.tasks[0]['description'])
        self.tasks[0]['description'] = 'User edited requirements'
        self.c.board.reset_mock()
        self.c.sync_background_prs()
        self.c.board.description.assert_not_called()
        self.assertEqual(self.tasks[0]['description'], 'User edited requirements')

    def test_active_timing_is_bucketed_but_usage_changes_are_immediate(self):
        self.c.store.update(self.id, state='review')
        clock = [100.0]
        self.c.store.timing.clock = lambda: clock[0]
        # Start an interval under the controlled monotonic clock.
        self.c.store.update(self.id, state='qa')
        self.c.sync_background_prs()
        self.c.board.reset_mock()
        clock[0] += 10
        self.c.sync_background_prs()
        self.c.board.description.assert_not_called()
        self.c.store.usage(self.id, 'codex', 'default', {'input_tokens': 7}, 2)
        self.c.sync_background_prs()
        self.c.board.description.assert_called_once()
        self.assertIn('codex:', self.tasks[0]['description'])
        self.c.board.reset_mock()
        clock[0] += 60
        self.c.sync_background_prs()
        self.c.board.description.assert_called_once()

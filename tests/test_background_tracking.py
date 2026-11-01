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

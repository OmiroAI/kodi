import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator
from kodi.github import pr_state


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.coordinator = Coordinator({'state_dir': self.directory.name,
                                        'board': {'steps': {'Done': 'done'}},
                                        'telegram_env': 'unused'})
        self.addCleanup(self.coordinator.store.db.close)
        self.coordinator.board = Mock()
        self.sender = patch('kodi.coordinator.send').start()
        self.addCleanup(patch.stopall)

    def job(self, number=1):
        job = self.coordinator.store.enqueue(str(number), 'system', 'Task', 'Requirements', str(number))
        self.coordinator.store.update(job['id'], state='human_review',
                                      pr_url=f'https://github.com/example/system/pull/{number}')
        return job['id']

    def test_open_even_approved_stays_in_human_review(self):
        job = self.job()
        with patch('kodi.github.gh', return_value=json.dumps({'state': 'OPEN', 'reviewDecision': 'APPROVED'})) as gh:
            self.coordinator.finish_prs()
        self.assertEqual(self.coordinator.store.get(job)['state'], 'human_review')
        gh.assert_called_once_with('pr', 'view', self.coordinator.store.get(job)['pr_url'], '--json', 'state')
        self.coordinator.board.move.assert_not_called()
        self.sender.assert_not_called()

    def test_terminal_outcomes_and_deduplication(self):
        for number, state, expected in [(1, 'MERGED', 'done'), (2, 'CLOSED', 'cancelled')]:
            job = self.job(number)
            with patch('kodi.github.pr_state', return_value=state):
                self.coordinator.finish_prs()
                self.coordinator.finish_prs()
            self.assertEqual(self.coordinator.store.get(job)['state'], expected)
            description = self.coordinator.board.description.call_args.args[1]
            self.assertIn('Kodi status: ' + expected, description)
            self.assertIn(self.coordinator.store.get(job)['pr_url'], self.sender.call_args.args[1])
            if expected == 'cancelled':
                self.assertIn('closed without merging', description)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.coordinator.board.move.call_count, 2)
        self.assertEqual(self.coordinator.board.move.call_args.args[-1], 'Done')

    def test_lookup_failure_isolated_and_response_details_suppressed(self):
        first, second = self.job(), self.job(2)
        with patch('kodi.github.pr_state', side_effect=[RuntimeError('private response'), 'MERGED']) as lookup:
            self.coordinator.finish_prs()
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(self.coordinator.store.get(first)['state'], 'human_review')
        self.assertEqual(self.coordinator.store.get(second)['state'], 'done')
        self.assertNotIn('private response', self.sender.call_args.args[1])

    def test_malformed_responses_do_not_transition(self):
        job = self.job()
        for response in ['broken json', 'null', '[]', '{}', '{"state":"APPROVED"}', '{"state":true}']:
            with patch('kodi.github.gh', return_value=response):
                self.coordinator.finish_prs()
            self.assertEqual(self.coordinator.store.get(job)['state'], 'human_review')
        self.sender.assert_not_called()

    def test_malformed_lookup_does_not_stop_next_pr(self):
        first, second = self.job(), self.job(2)
        with patch('kodi.github.gh', side_effect=['[]', '{"state":"MERGED"}']) as lookup:
            self.coordinator.finish_prs()
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(self.coordinator.store.get(first)['state'], 'human_review')
        self.assertEqual(self.coordinator.store.get(second)['state'], 'done')
        self.sender.assert_called_once()

    def test_terminal_state_is_durable_before_side_effects_and_retries_after_restart(self):
        for number, state, expected in [(1, 'MERGED', 'done'), (2, 'CLOSED', 'cancelled')]:
            with self.subTest(state=state):
                job = self.job(number)

                def fail_board(*args):
                    # Read through a separate connection to verify the commit precedes delivery.
                    observer = Coordinator(self.coordinator.config)
                    try:
                        self.assertEqual(observer.store.get(job)['state'], expected)
                    finally:
                        observer.store.db.close()
                    raise RuntimeError('board unavailable')

                self.coordinator.board.move.side_effect = fail_board
                self.sender.side_effect = RuntimeError('notification unavailable')
                with patch('kodi.github.pr_state', return_value=state):
                    self.coordinator.finish_prs()
                self.coordinator.board.move.side_effect = None
                self.sender.side_effect = None
                self.sender.reset_mock()
                restarted = Coordinator(self.coordinator.config)
                self.addCleanup(restarted.store.db.close)
                restarted.board = self.coordinator.board
                with patch('kodi.github.pr_state') as lookup:
                    restarted.finish_prs()
                    restarted.finish_prs()
                lookup.assert_not_called()
                self.sender.assert_called_once()
                self.assertIn(expected, self.coordinator.board.description.call_args.args[1])

    def test_board_failure_retries_without_repeating_notification(self):
        job = self.job()
        self.coordinator.board.description.side_effect = [RuntimeError('board unavailable'), None]
        with patch('kodi.github.pr_state', return_value='MERGED') as lookup:
            self.coordinator.finish_prs()
            self.assertEqual(self.coordinator.store.get(job)['state'], 'done')
            # A new coordinator uses the same durable state after a restart.
            restarted = Coordinator(self.coordinator.config)
            self.addCleanup(restarted.store.db.close)
            restarted.board = self.coordinator.board
            restarted.finish_prs()
            restarted.finish_prs()
        lookup.assert_called_once()
        self.assertEqual(self.coordinator.board.description.call_count, 2)
        self.sender.assert_called_once()

    def test_notification_failure_retries_without_repeating_board_work(self):
        self.job()
        self.sender.side_effect = [RuntimeError('delivery failed'), None]
        with patch('kodi.github.pr_state', return_value='CLOSED'):
            for _ in range(3):
                self.coordinator.finish_prs()
        self.assertEqual(self.sender.call_count, 2)
        self.coordinator.board.move.assert_called_once()
        self.coordinator.board.description.assert_called_once()

    def test_move_failure_retries_and_tick_runs_monitor(self):
        self.job()
        self.coordinator.board.move.side_effect = [RuntimeError('unavailable'), None]
        with patch('kodi.github.pr_state', return_value='MERGED'), \
                patch.object(self.coordinator, 'sync'), \
                patch.object(self.coordinator, 'finish_checks'), \
                patch.object(self.coordinator.store, 'claim', return_value=None):
            self.coordinator.tick()
            self.coordinator.tick()
        self.assertEqual(self.coordinator.board.move.call_count, 2)
        self.coordinator.board.description.assert_called_once()
        self.sender.assert_called_once()


    def test_ci_pending_closed_pr_reconciles_before_check_polling(self):
        job = self.job()
        self.coordinator.store.update(job, state='ci_pending', commit_sha='a'*40)
        with patch('kodi.github.pr_state', return_value='CLOSED'), patch('kodi.github.checks_pass') as checks:
            self.coordinator.finish_prs()
            self.coordinator.finish_prs()
        checks.assert_not_called()
        self.assertEqual(self.coordinator.store.get(job)['state'], 'cancelled')
        self.assertFalse(self.coordinator.store.gate(job, 'a'*40))

    def test_terminal_ci_blocks_once_without_repair_or_passing_gate(self):
        job = self.job()
        self.coordinator.config['repos'] = {'system': {'qa_suite': 'system'}}
        self.coordinator.store.update(job, state='ci_pending', commit_sha='a'*40)
        def failed(*args, **kwargs):
            kwargs['diagnostics']['terminal'] = True
            return False
        with patch('kodi.github.authenticated', return_value=True), \
                patch('kodi.github.pull', return_value={'state': 'OPEN', 'headRefOid': 'a'*40}), \
                patch.object(self.coordinator.store, 'gate', return_value=True), \
                patch('kodi.github.checks_pass', side_effect=failed) as checks:
            self.coordinator.finish_checks()
            self.coordinator.finish_checks()
        checks.assert_called_once()
        self.assertEqual(self.coordinator.store.get(job)['state'], 'blocked')
        from kodi.failures import latest
        self.assertEqual(latest(self.coordinator.store, job, 'ci', 'a'*40)['category'], 'unknown')
        self.assertFalse(self.coordinator.store.gate(job, 'a'*40))


class PRStateTests(unittest.TestCase):
    def test_invalid_response_shapes_are_rejected(self):
        for value in (None, [], 'MERGED', 1, True, {}, {'state': ['MERGED']}):
            with self.subTest(value=value), patch('kodi.github.gh', return_value=json.dumps(value)):
                with self.assertRaises(ValueError):
                    pr_state('url')

    def test_read_only_states(self):
        for state in ('OPEN', 'MERGED', 'CLOSED'):
            with patch('kodi.github.gh', return_value=json.dumps({'state': state})):
                self.assertEqual(pr_state('url'), state)

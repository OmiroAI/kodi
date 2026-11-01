import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from kodi import delayed_review as retry
from kodi.failures import DeliveryFailure, provider_error, provider_failure, classify
from kodi.store import Store


class DelayedReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'queue.db'
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.db.close())
        self.job = self.store.enqueue('fixture', 'example', 'Example', 'Requirement')
        self.store.update(self.job['id'], state='review', commit_sha='candidate')
        self.job = self.store.get(self.job['id'])
        self.now = 100
        self.validate = Mock()
        self.reconcile = Mock()
        self.operation = Mock(side_effect=DeliveryFailure('transport_timeout'))

    def run_review(self, config=None):
        return retry.execute(self.store, self.job, 'candidate', config or {}, {'provider':'example'},
                             self.operation, self.validate, self.reconcile, lambda:self.now)

    def wait(self):
        with self.assertRaises(retry.ReviewWaiting): self.run_review()

    def test_fake_clock_wait_restart_same_candidate_success(self):
        self.wait()
        saved = retry.latest(self.store, self.job['id'])
        self.assertEqual((saved['next_retry_at'], saved['consumed']), (130, 1))
        self.assertEqual(self.store.get(self.job['id'])['state'], 'review_wait')
        self.wait()
        self.assertEqual(self.operation.call_count, 1)
        self.store.db.close()
        self.store = Store(self.path)
        self.now = 130
        self.operation.side_effect = None
        self.operation.return_value = 'reviewed original candidate'
        self.assertEqual(self.run_review({'worker_timeout':42}), 'reviewed original candidate')
        self.assertEqual(self.operation.call_args.args[0]['timeout'], 900)
        self.assertEqual(retry.latest(self.store, self.job['id'])['consumed'], 2)
        with self.assertRaises(RuntimeError): self.run_review()
        self.assertEqual(self.operation.call_count, 2)
        self.assertEqual(self.store.get(self.job['id'])['attempt'], 0)

    def test_budget_exhaustion_and_configuration_does_not_replenish(self):
        self.wait(); self.now = 130; self.wait(); self.now = 250
        with self.assertRaises(DeliveryFailure): self.run_review()
        self.assertEqual(retry.latest(self.store, self.job['id'])['status'], 'blocked')
        with self.assertRaises(RuntimeError): self.run_review({'worker_timeout':25})
        self.assertEqual(self.operation.call_count, 3)

    def test_provider_deadlines_and_unknowns(self):
        self.assertEqual(retry.deadline('60', 100), 160)
        self.assertEqual(retry.deadline('Thu, 01 Jan 1970 00:03:20 GMT', 100), 200)
        for value in ('later', -1, float('nan')):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                retry.deadline(value, 100)
        self.operation.side_effect = DeliveryFailure('http_429', 1000)
        with self.assertRaises(DeliveryFailure): self.run_review()
        self.assertEqual(retry.latest(self.store, self.job['id'])['status'], 'blocked')
        self.reconcile.assert_not_called()

    def test_uncertain_or_concurrent_request_never_replaced(self):
        def interrupted(effective):
            with self.assertRaises(RuntimeError): self.run_review()
            raise KeyboardInterrupt()
        self.operation.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt): self.run_review()
        self.assertEqual(retry.latest(self.store, self.job['id'])['status'], 'claimed')
        with self.assertRaises(RuntimeError): self.run_review()
        self.assertEqual(self.operation.call_count, 1)

    def test_pause_cancel_and_drift_are_rechecked_before_dispatch(self):
        self.wait(); self.now = 130
        self.validate.side_effect = RuntimeError('Authorization withdrawn')
        with self.assertRaises(RuntimeError): self.run_review()
        self.assertEqual(self.operation.call_count, 1)
        self.job = dict(self.job, requirements='Changed')
        with self.assertRaises(RuntimeError): self.run_review()
        self.assertEqual(retry.latest(self.store, self.job['id'])['consumed'], 1)

    def test_remote_uncertainty_and_unknown_failures_do_not_release(self):
        self.reconcile.side_effect = RuntimeError('Unknown remote state')
        with self.assertRaises(DeliveryFailure): self.run_review()
        self.assertEqual(retry.latest(self.store, self.job['id'])['status'], 'blocked')
        self.assertEqual(self.store.get(self.job['id'])['state'], 'review')
        for error in (RuntimeError('503'), DeliveryFailure('explicit_quota'),
                      DeliveryFailure('http_401'), DeliveryFailure('blocking_review')):
            self.assertFalse(classify(error)['retry_eligible'])
        for status in (500, 502, 503, 504):
            self.assertTrue(classify(provider_failure('HTTP '+str(status)))['retry_eligible'])
        self.assertTrue(classify(provider_error(TimeoutError()))['retry_eligible'])

    def test_result_drift_is_rechecked(self):
        self.operation.side_effect = lambda effective: setattr(self.validate, 'side_effect', RuntimeError('drift'))
        with self.assertRaises(RuntimeError): self.run_review()
        self.assertEqual(retry.latest(self.store, self.job['id'])['status'], 'claimed')

    def test_coordinator_resumes_only_review_preserving_qa_and_pr(self):
        from kodi.coordinator import Coordinator
        from unittest.mock import patch
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = dict(state_dir=self.directory.name, reviewer='gemini',
                        repos={'example':dict(enabled=True)})
        checkout = Path(self.directory.name)/'jobs'/str(self.job['id'])/'checkout'
        checkout.mkdir(parents=True)
        sha = self.job['commit_sha']
        self.store.update(self.job['id'], pr_url='https://example.invalid/pull/1')
        self.job = self.store.get(self.job['id'])
        self.store.evidence(self.job['id'], sha, 'author', {'test_steps':['Check behavior']})
        self.store.evidence(self.job['id'], sha, 'qa', {'sha':sha, 'passed':True})
        c.progress = Mock(); c.notification = Mock(); c.move = Mock()
        for method in ('finish_prs', 'sync', 'finish_checks', 'request_reviews', 'maintain_merges'):
            setattr(c, method, Mock())
        verdict = dict(approved=True, criteria_met=True, simple=True, blocking_findings=[], summary='Verified')
        def git(checkout, *args):
            if args == ('status', '--porcelain'): return ''
            return sha
        with patch('kodi.coordinator.git', side_effect=git), patch(
                'kodi.coordinator.review_material', return_value=('diff','',[])), patch(
                'kodi.host_recovery.preview', return_value={'blockers':[]}), patch(
                'kodi.coordinator.run_worker', side_effect=[DeliveryFailure('transport_timeout'), (verdict, {}, 0)]) as worker, patch(
                'kodi.coordinator.qa') as qa, patch('kodi.delayed_review.time.time', side_effect=lambda:self.now):
            with self.assertRaises(retry.ReviewWaiting):
                c.scheduled_review(self.job, checkout, sha, {'test_steps':['Check behavior']})
            c._tick()
            self.assertEqual(worker.call_count, 1)
            self.now = 130
            c._tick()
            self.assertEqual(worker.call_count, 2)
            self.assertTrue(all(call.args[4] == 'review' for call in worker.call_args_list))
            qa.assert_not_called()
        current = self.store.get(self.job['id'])
        self.assertEqual((current['state'], current['commit_sha'], current['pr_url']),
                         ('validated', sha, self.job['pr_url']))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='qa'").fetchone()[0], 1)
        self.assertTrue(self.store.gate(self.job['id'], sha))

    def test_provider_admission_is_shared_without_consuming_attempt(self):
        self.wait()
        other = self.store.enqueue('second', 'different', 'Second', 'Requirement')
        self.store.update(other['id'], state='review', commit_sha='other')
        other = self.store.get(other['id'])
        operation = Mock()
        with self.assertRaises(retry.ReviewWaiting):
            retry.execute(self.store, other, 'other', {}, {'provider':'example'},
                          operation, self.validate, self.reconcile, lambda:100)
        operation.assert_not_called()
        self.assertEqual(retry.latest(self.store, other['id'])['consumed'], 0)

    def test_waiting_releases_ownership_only_after_remote_reconciliation(self):
        other = self.store.enqueue('second', 'example', 'Second', 'Requirement')
        self.assertIsNotNone(self.store.repository_blocker(other))
        self.wait()
        self.assertIsNone(self.store.repository_blocker(other))
        token = self.store.begin_invocation(self.job['id'], 'review_retry')
        self.assertIsNotNone(self.store.repository_blocker(other))
        with self.store.db:
            self.store.db.execute("UPDATE invocations SET status='complete' WHERE id=?", (token,))
        self.assertIsNone(self.store.repository_blocker(other))

    def test_window_expiry_is_durable_without_dispatch(self):
        self.wait(); self.now = 1001
        with self.assertRaises(RuntimeError): self.run_review()
        self.assertEqual(retry.latest(self.store, self.job['id'])['status'], 'blocked')
        self.assertEqual(self.operation.call_count, 1)

    def test_documented_absolute_reset_is_never_shortened(self):
        self.operation.side_effect = DeliveryFailure('http_429', reset_at=400)
        self.wait()
        self.assertEqual(retry.latest(self.store, self.job['id'])['next_retry_at'], 400)
        self.now = 399
        self.wait()
        self.assertEqual(self.operation.call_count, 1)

    def test_two_database_connections_cannot_dispatch_same_claim(self):
        second = Store(self.path)
        self.addCleanup(second.db.close)
        other_operation = Mock()
        def invoke(effective):
            with self.assertRaises(RuntimeError):
                retry.execute(second, second.get(self.job['id']), 'candidate', {}, {'provider':'example'},
                              other_operation, Mock(), Mock(), lambda:100)
            return 'reviewed'
        self.operation.side_effect = invoke
        self.assertEqual(self.run_review(), 'reviewed')
        other_operation.assert_not_called()

    def test_actual_authoritative_pause_cancel_and_edit_refuse_review(self):
        from kodi.coordinator import Coordinator
        from unittest.mock import patch
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = dict(repos={'example':dict(enabled=True)}, board=dict(steps={'Review':'review'}))
        c.board = Mock()
        c.review = Mock()
        self.store.update(self.job['id'], board_id='task')
        self.store.evidence(self.job['id'], 'candidate', 'qa', dict(sha='candidate', passed=True))
        checkout = Path(self.directory.name)
        def git(checkout, *args):
            return '' if args == ('status', '--porcelain') else 'candidate'
        for step, description in [('paused','Requirement'), ('cancelled','Requirement'), ('review','Edited')]:
            with self.subTest(step=step), patch('kodi.coordinator.git', side_effect=git):
                c.board.tasks.return_value = [dict(id='task', workflow_step_id=step, description=description)]
                with self.assertRaises(RuntimeError):
                    c.scheduled_review(self.store.get(self.job['id']), checkout, 'candidate', {})
        c.review.assert_not_called()

    def test_malformed_and_nontransport_failures_persist_block_without_retry(self):
        for error in (DeliveryFailure('http_429', 'later'), DeliveryFailure('explicit_quota'),
                      DeliveryFailure('http_403'), RuntimeError('unknown'),
                      DeliveryFailure('blocking_review')):
            with self.subTest(error=error):
                store = Store(':memory:')
                try:
                    job = store.enqueue('fixture', 'example', 'Example', 'Requirement')
                    operation = Mock(side_effect=error)
                    with self.assertRaises(type(error)):
                        retry.execute(store, job, 'candidate', {}, {}, operation, Mock(), Mock(), lambda:100)
                    self.assertEqual(retry.latest(store, job['id'])['status'], 'blocked')
                    with self.assertRaises(RuntimeError):
                        retry.execute(store, job, 'candidate', {}, {}, operation, Mock(), Mock(), lambda:130)
                    self.assertEqual(operation.call_count, 1)
                finally: store.db.close()

    def test_pause_during_wait_does_not_move_authoritative_task(self):
        from kodi.coordinator import Coordinator
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = {'state_dir':self.directory.name}
        c.scheduled_review = Mock(side_effect=retry.ReviewAuthorizationChanged('paused'))
        c.move = Mock(); c.progress = Mock()
        c.resume_review(self.job)
        self.assertEqual(self.store.get(self.job['id'])['state'], 'blocked')
        c.move.assert_not_called()
        self.store.update(self.job['id'], state='cancelled')
        c.resume_review(self.store.get(self.job['id']))
        self.assertEqual(self.store.get(self.job['id'])['state'], 'cancelled')

    def test_merge_intent_is_not_released_during_review_wait(self):
        other = self.store.enqueue('second', 'example', 'Second', 'Requirement')
        self.wait()
        self.store.evidence(self.job['id'], 'candidate', 'merge_maintenance', {'expected_head':'candidate'})
        self.assertIsNotNone(self.store.repository_blocker(other))

    def test_owner_notification_keys_survive_restart(self):
        from kodi.coordinator import Coordinator
        from unittest.mock import patch
        c = Coordinator.__new__(Coordinator)
        c.store = self.store
        c.config = {'telegram_env':'fixture'}
        with patch('kodi.coordinator.send') as send:
            c.notification('review-wait:fixture:1', 'Waiting')
            self.store.db.close()
            self.store = Store(self.path)
            c.store = self.store
            c.notification('review-wait:fixture:1', 'Waiting')
            send.assert_called_once()

    def test_invalid_timeout_never_dispatches_or_reserves(self):
        for timeout in (True, 0, -1, '900', 86401):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.run_review({'worker_timeout':timeout})
        self.operation.assert_not_called()
        self.assertIsNone(retry.latest(self.store, self.job['id']))

    def test_typed_http_quota_is_not_a_throttle_retry(self):
        from io import BytesIO
        from urllib.error import HTTPError
        from email.message import Message
        headers = Message(); headers['Retry-After'] = '60'
        quota = HTTPError('https://example.invalid', 429, 'throttled', headers,
                          BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
        self.assertEqual(classify(provider_error(quota))['category'], 'quota')
        throttle = HTTPError('https://example.invalid', 429, 'throttled', headers, BytesIO(b''))
        error = provider_error(throttle)
        self.assertTrue(classify(error)['retry_eligible'])
        self.assertEqual(error.retry_after, '60')

    def test_changed_claim_fences_result(self):
        def replace(effective):
            record = retry.latest(self.store, self.job['id'])
            record['claim'] = 'replacement'
            self.store.evidence(self.job['id'], 'candidate', 'delayed_review', record)
            return 'stale verdict'
        self.operation.side_effect = replace
        with self.assertRaisesRegex(RuntimeError, 'Stale review result'):
            self.run_review()
        self.assertEqual(retry.latest(self.store, self.job['id'])['status'], 'claimed')

    def test_new_candidate_after_failed_review_preserves_old_budget(self):
        self.operation.side_effect = RuntimeError('provider failure')
        with self.assertRaises(RuntimeError): self.run_review()
        old = retry.latest(self.store, self.job['id'])
        self.store.update(self.job['id'], commit_sha='rebased')
        self.store.evidence(self.job['id'], 'rebased', 'qa', {'sha':'rebased', 'passed':True})
        self.operation.side_effect = None
        self.operation.return_value = 'accepted'
        self.assertEqual(retry.execute(self.store, self.store.get(self.job['id']), 'rebased', {},
                         {'provider':'example'}, self.operation, self.validate, self.reconcile,
                         lambda:10000), 'accepted')
        self.assertEqual(retry.latest(self.store, self.job['id'], 'candidate'), old)
        self.assertEqual(retry.latest(self.store, self.job['id'])['consumed'], 1)
        self.store.update(self.job['id'], commit_sha='candidate')
        self.store.evidence(self.job['id'], 'candidate', 'qa', {'sha':'candidate', 'passed':True})
        with self.assertRaises(RuntimeError): self.run_review()
        self.assertEqual(self.operation.call_count, 2)

    def test_new_candidate_requires_qa_and_preserves_uncertain_claim(self):
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain):
                record = dict(status='claimed' if uncertain else 'blocked', binding='old')
                self.store.evidence(self.job['id'], 'candidate', 'delayed_review', record)
                self.store.update(self.job['id'], commit_sha='new')
                if uncertain:
                    self.store.evidence(self.job['id'], 'new', 'qa', {'sha':'new', 'passed':True})
                with self.assertRaises(RuntimeError):
                    retry.execute(self.store, self.store.get(self.job['id']), 'new', {}, {},
                                  self.operation, self.validate, self.reconcile)
                self.assertEqual(retry.latest(self.store, self.job['id']), record)
        self.operation.assert_not_called()

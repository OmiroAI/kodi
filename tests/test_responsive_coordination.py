"""Disposable control-plane regressions, not independent worker acceptance."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator, WorkPending
from kodi.store import Store, StaleClaim


class ResponsiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {'state_dir': self.temp.name, 'repos': {}}
        self.c = Coordinator(self.config)
        self.addCleanup(self.c.store.db.close)
        self.job = self.c.store.enqueue('example:1', 'project', 'Example', 'Requirements')

    def quiet(self, coordinator):
        for name in ('finish_prs', 'sync', 'finish_checks', 'request_reviews', 'maintain_merges'):
            setattr(coordinator, name, Mock())

    def test_held_stages_release_lock_and_control_cycles_continue(self):
        for stage in ('coding', 'qa', 'review'):
            with self.subTest(stage=stage):
                entered, release = threading.Event(), threading.Event()
                self.c.store.update(self.job['id'], state='coding')
                claimed = self.c.store.get(self.job['id'])

                def held(worker, job):
                    worker.store.update(job['id'], state=stage, commit_sha='candidate')
                    entered.set()
                    if not release.wait(5): raise RuntimeError('fixture timeout')
                    worker.store.update(job['id'], state='blocked')

                with patch.object(Coordinator, 'process', held):
                    thread = self.c.dispatch(claimed)
                    try:
                        self.assertTrue(entered.wait(2))
                        other = Coordinator(self.config)
                        self.addCleanup(other.store.db.close)
                        self.quiet(other)
                        for _ in range(3): other.tick()
                        other.sync.assert_called()
                        self.assertEqual(other.finish_checks.call_count, 3)
                        self.assertEqual(other.maintain_merges.call_count, 3)
                        self.assertTrue(thread.is_alive())
                        self.assertEqual(other.store.get(self.job['id'])['state'], stage)
                        self.assertEqual(other.store.get(self.job['id'])['commit_sha'], 'candidate')
                        self.assertEqual(other.store.db.execute(
                            "SELECT COUNT(*) FROM invocations WHERE status='running'").fetchone()[0], 1)
                        self.assertIsNone(other.store.claim())
                        metrics = other.store.db.execute(
                            "SELECT seconds FROM coordination_metrics WHERE event='lock_hold'").fetchall()
                        self.assertTrue(all(row[0] < 1 for row in metrics))
                    finally:
                        release.set()
                        thread.join(5)
                    self.assertFalse(thread.is_alive())

    def test_concurrent_ticks_dispatch_once(self):
        entered, release = threading.Event(), threading.Event()
        calls, errors = [], []

        def held(worker, job):
            calls.append(job['id'])
            entered.set()
            release.wait(5)
            worker.store.update(job['id'], state='blocked')

        def cycle():
            coordinator = Coordinator(self.config)
            self.quiet(coordinator)
            try: coordinator.tick()
            except Exception as error: errors.append(error)
            finally: coordinator.store.db.close()

        with patch.object(Coordinator, 'process', held):
            threads = [threading.Thread(target=cycle) for _ in range(2)]
            for thread in threads: thread.start()
            try:
                for thread in threads: thread.join(3)
                self.assertTrue(entered.wait(2))
                self.assertEqual(calls, [self.job['id']])
                self.assertFalse(errors)
            finally:
                release.set()
        # Wait for the detached fixture to finish before removing its database.
        for thread in threading.enumerate():
            if thread.name.startswith('kodi-'): thread.join(5)

    def test_pause_fences_result_without_erasing_budget_or_replacing_worker(self):
        self.c.store.update(self.job['id'], state='review', commit_sha='candidate', attempt=2)
        token = self.c.store.begin_invocation(self.job['id'], 'coding')
        worker = Store(Path(self.temp.name)/'kodi.sqlite', token)
        self.addCleanup(worker.db.close)
        worker.reserve_recovery(self.job['id'], 'review', 'candidate', 2)
        self.c.store.update(self.job['id'], state='paused')
        for operation in (
            lambda: worker.evidence(self.job['id'], 'candidate', 'review', {'approved': True}),
            lambda: worker.update(self.job['id'], state='validated'),
            lambda: worker.reserve_recovery(self.job['id'], 'review', 'candidate', 2),
        ):
            with self.assertRaises(StaleClaim): operation()
        self.assertEqual(self.c.store.get(self.job['id'])['attempt'], 2)
        self.assertEqual(self.c.store.db.execute('SELECT attempts FROM recovery_attempts').fetchone()[0], 1)
        self.assertIsNone(self.c.store.begin_invocation(self.job['id'], 'coding'))
        self.assertFalse(self.c.store.gate(self.job['id'], 'candidate'))

    def test_pause_then_restore_state_still_fences_original_generation(self):
        self.c.store.update(self.job['id'], state='coding')
        token = self.c.store.begin_invocation(self.job['id'], 'coding')
        worker = Store(Path(self.temp.name)/'kodi.sqlite', token)
        self.addCleanup(worker.db.close)
        self.c.store.update(self.job['id'], state='paused')
        self.c.store.update(self.job['id'], state='coding')
        with self.assertRaises(StaleClaim): worker.update(self.job['id'], state='validated')
        self.assertIsNone(self.c.store.begin_invocation(self.job['id'], 'coding'))

    def test_control_metadata_refresh_does_not_fence_worker(self):
        self.c.store.update(self.job['id'], state='review', commit_sha='candidate')
        token = self.c.store.begin_invocation(self.job['id'], 'coding')
        worker = Store(Path(self.temp.name)/'kodi.sqlite', token)
        self.addCleanup(worker.db.close)
        self.c.store.update(self.job['id'], board_id='example-board', title='Updated title',
                            error=None, state='review', commit_sha='candidate')
        worker.evidence(self.job['id'], 'candidate', 'qa', {'passed': True})
        worker.update(self.job['id'], state='validated')
        self.assertEqual(self.c.store.get(self.job['id'])['state'], 'validated')
        self.assertEqual(self.c.store.get(self.job['id'])['board_id'], 'example-board')
        self.assertEqual(self.c.store.db.execute(
            "SELECT COUNT(*) FROM evidence WHERE kind='qa'").fetchone()[0], 1)

    def test_changed_candidate_or_requirements_fence_results(self):
        for field, value in (('commit_sha', 'replacement'), ('requirements', 'New scope')):
            with self.subTest(field=field):
                job = self.c.store.enqueue('example:'+field, 'project', 'Example', 'Original')
                token = self.c.store.begin_invocation(job['id'], 'coding')
                worker = Store(Path(self.temp.name)/'kodi.sqlite', token)
                self.addCleanup(worker.db.close)
                self.c.store.update(job['id'], **{field: value})
                with self.assertRaises(StaleClaim):
                    worker.evidence(job['id'], 'candidate', 'review', {'approved': True})
                self.assertEqual(self.c.store.get(job['id'])[field], value)

    def test_invocation_ownership_is_per_job_and_claim_remains_state_limited(self):
        self.c.store.update(self.job['id'], state='human_review')
        token = self.c.store.begin_invocation(self.job['id'], 'base')
        self.assertIsNotNone(token)
        next_job = self.c.store.enqueue('example:2', 'project', 'Second', 'Requirements')
        last_job = self.c.store.enqueue('example:3', 'project', 'Third', 'Requirements')
        claimed = self.c.store.claim()
        self.assertEqual(claimed['id'], next_job['id'])
        self.assertIsNone(self.c.store.claim())
        self.assertIsNotNone(self.c.store.begin_invocation(claimed['id'], 'coding'))
        self.assertIsNone(self.c.store.begin_invocation(self.job['id'], 'base'))
        self.assertEqual(self.c.store.get(last_job['id'])['state'], 'queued')

    def test_uncertain_queued_job_is_skipped_without_duplicate_dispatch(self):
        self.assertIsNotNone(self.c.store.begin_invocation(self.job['id'], 'coding'))
        next_job = self.c.store.enqueue('example:2', 'project', 'Second', 'Requirements')
        restarted = Coordinator(self.config)
        self.addCleanup(restarted.store.db.close)
        self.assertEqual(restarted.store.claim()['id'], next_job['id'])
        self.assertIsNone(restarted.store.begin_invocation(self.job['id'], 'coding'))

    def test_completed_coding_hands_publication_to_control_cycle(self):
        source = Path(self.temp.name)/'source'
        (source/'.git').mkdir(parents=True)
        self.config['repos'] = {'project': {'source': str(source), 'qa_suite': 'system'}}
        self.c.store.update(self.job['id'], state='coding')
        job = self.c.store.get(self.job['id'])

        def git(checkout, *args):
            if args[:3] == ('diff', '--cached', '--name-only'): return 'example.py'
            if args == ('rev-parse', 'HEAD'): return 'candidate'
            return ''

        def review(worker, job, checkout, sha, author):
            worker.store.evidence(job['id'], sha, 'review', {
                'approved': True, 'criteria_met': True, 'simple': True,
                'blocking_findings': [], 'author_family': 'openai', 'reviewer_family': 'google'})
            return {'approved': True}

        with patch('kodi.coordinator.git', side_effect=git),                 patch('kodi.coordinator.subprocess.run'),                 patch('kodi.coordinator.run_worker', return_value=({'test_steps': ['Try example']}, {}, 1)),                 patch('kodi.coordinator.qa', return_value={'passed': True}),                 patch.object(Coordinator, 'review', review),                 patch.object(Coordinator, 'publish') as publish:
            thread = self.c.dispatch(job)
            thread.join(5)
            self.assertFalse(thread.is_alive())
            publish.assert_not_called()
            self.assertEqual(self.c.store.get(job['id'])['state'], 'validated')
            self.assertTrue(self.c.store.gate(job['id'], 'candidate'))
            self.quiet(self.c)
            self.c.tick()
            publish.assert_called_once_with(job['id'])

    def test_uncertain_start_and_restart_retain_ownership(self):
        claimed = self.c.store.claim()
        with patch('kodi.coordinator.threading.Thread.start', side_effect=RuntimeError('uncertain start')):
            with self.assertRaises(RuntimeError): self.c.dispatch(claimed)
        with self.c.store.db:
            self.c.store.db.execute("UPDATE invocations SET updated='2000-01-01T00:00:00+00:00'")
        restarted = Coordinator(self.config)
        self.addCleanup(restarted.store.db.close)
        self.quiet(restarted)
        with patch.object(restarted, 'dispatch') as dispatch:
            restarted.tick()
            dispatch.assert_not_called()
        self.assertEqual(restarted.store.get(claimed['id'])['state'], 'coding')
        self.assertIsNone(restarted.store.begin_invocation(claimed['id'], 'coding'))

    def test_base_repair_is_dispatched_instead_of_run_under_control_lock(self):
        self.c.config['repos'] = {'project': {'source': self.temp.name}}
        self.c.store.update(self.job['id'], state='validated', commit_sha='candidate')
        self.c._control_cycle = True
        with patch.object(self.c, 'refresh_source'), patch('kodi.coordinator.git', return_value='base'), \
                patch('kodi.coordinator.subprocess.run', return_value=Mock(returncode=1)), \
                patch.object(self.c, 'dispatch') as dispatch, patch.object(self.c, '_refresh_base') as repair:
            with self.assertRaises(WorkPending): self.c.refresh_base(self.job['id'])
            dispatch.assert_called_once()
            repair.assert_not_called()

    def test_submission_qa_releases_lock_and_cancel_fences_handoff(self):
        import fcntl
        from kodi.submission import independent_work
        original = self.c.store
        original.update(self.job['id'], state='qa')
        other = Coordinator(self.config)
        self.addCleanup(other.store.db.close)
        self.quiet(other)
        with (Path(self.temp.name)/'delivery.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaises(StaleClaim):
                with independent_work(self.c, self.job['id'], lock):
                    other.tick()
                    other.sync.assert_called_once()
                    other.store.update(self.job['id'], state='cancelled')
        self.assertIs(self.c.store, original)
        self.assertEqual(original.get(self.job['id'])['state'], 'cancelled')
        self.assertEqual(original.invocation_status(self.job['id'])['status'], 'stale')

    def test_unresolved_merge_intent_prevents_unrelated_publication(self):
        pending = self.c.store.enqueue('example:2', 'project', 'Pending', 'Requirements')
        self.c.store.update(pending['id'], state='blocked', commit_sha='old')
        self.c.store.evidence(pending['id'], 'old', 'merge_maintenance', {'expected_head': 'old'})
        self.c.store.update(self.job['id'], state='validated', commit_sha='candidate')
        self.quiet(self.c)
        with patch.object(self.c, 'publish') as publish:
            self.c.tick()
            publish.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'Unresolved delivery'):
                self.c.publish_control(self.job['id'])

    def test_pending_publication_is_distinct_from_busy_mutation_lock(self):
        import fcntl
        with patch.object(self.c, 'publish', side_effect=WorkPending()) as publish:
            self.c.publish_control(self.job['id'])
            publish.assert_called_once_with(self.job['id'])
            self.assertFalse(self.c._control_cycle)
            with (Path(self.temp.name)/'delivery.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, 'Delivery mutation is busy'):
                    self.c.publish_control(self.job['id'])
            self.assertEqual(publish.call_count, 1)

    def test_cancelled_candidate_cannot_be_published(self):
        self.c.config['repos'] = {'project': {}}
        self.c.store.update(self.job['id'], state='cancelled', commit_sha='candidate')
        with patch('kodi.coordinator.github.publish') as publish:
            with self.assertRaisesRegex(RuntimeError, 'validated candidate'):
                self.c.publish_control(self.job['id'])
            publish.assert_not_called()

    def test_approval_waiting_and_progress_are_distinct_and_survive_restart(self):
        self.c.store.update(self.job['id'], commit_sha='candidate')
        job = self.c.store.get(self.job['id'])
        self.c.approval_timing(job, 'approval_observed')
        restarted = Coordinator(self.config)
        self.addCleanup(restarted.store.db.close)
        restarted.approval_timing(job, 'approval_observed')
        self.assertEqual(self.c.store.db.execute('SELECT COUNT(*) FROM coordination_metrics').fetchone()[0], 1)
        restarted.approval_timing(job, 'approval_progress')
        row = self.c.store.db.execute("SELECT * FROM coordination_metrics WHERE event='approval_progress'").fetchone()
        self.assertEqual(row['candidate'], 'candidate')
        self.assertGreaterEqual(row['seconds'], 0)

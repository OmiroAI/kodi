import tempfile
import unittest
from pathlib import Path

from kodi.store import Store
from kodi.job_control import inspect, control, boundary, Paused


class JobControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'queue.sqlite')
        self.addCleanup(self.store.db.close)
        self.job = self.store.enqueue('one', 'example', 'Repair', 'Requirements')['id']

    def act(self, action, job=None, dependency=None):
        job = job or self.job
        return control(self.store, job, action, inspect(self.store, job)['digest'],
                       'operator incident reference', dependency)

    def test_pause_resume_preserves_checkpoint_evidence_and_budget(self):
        self.store.update(self.job, state='qa', attempt=2, commit_sha='a'*40)
        self.store.evidence(self.job, 'a'*40, 'author', {'test_steps': ['inspect']})
        self.store.reserve_recovery(self.job, 'qa', 'a'*40, 3)
        self.act('pause')
        self.assertEqual(self.store.get(self.job)['state'], 'paused')
        self.assertIsNone(self.store.claim())
        self.act('resume')
        self.assertEqual(self.store.get(self.job)['state'], 'qa')
        self.assertEqual(self.store.get(self.job)['attempt'], 2)
        self.assertEqual(self.store.db.execute('SELECT attempts FROM recovery_attempts').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='author'").fetchone()[0], 1)

    def test_running_worker_pauses_only_at_boundary_and_cannot_resume_uncertain(self):
        self.store.update(self.job, state='coding')
        token = self.store.begin_invocation(self.job, 'coding')
        self.act('pause')
        self.assertEqual(self.store.get(self.job)['state'], 'coding')
        worker = Store(Path(self.tmp.name)/'queue.sqlite', token)
        self.addCleanup(worker.db.close)
        worker.update(self.job, state='qa', commit_sha='a'*40)
        with self.assertRaises(Paused):
            boundary(worker, self.job, 'qa')
        with self.assertRaisesRegex(ValueError, 'Uncertain'):
            self.act('resume')

    def test_stale_inspection_and_changed_checkpoint_identity_refused(self):
        digest = inspect(self.store, self.job)['digest']
        self.store.update(self.job, requirements='Changed')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            control(self.store, self.job, 'pause', digest, 'operator')
        self.act('pause')
        self.store.update(self.job, requirements='Changed again')
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.act('resume')

    def test_urgent_job_takes_next_slot_without_releasing_blocked_owner(self):
        self.store.update(self.job, state='blocked', attempt=1, commit_sha='a'*40)
        ordinary = self.store.enqueue('two', 'example', 'Ordinary', 'Requirements')['id']
        urgent = self.store.enqueue('three', 'example', 'Recovery', 'Repair coordinator')['id']
        self.act('prioritize', urgent)
        self.assertEqual(self.store.repository_blocker(self.store.get(urgent))['id'], self.job)
        self.act('prioritize', urgent, self.job)
        self.assertEqual(self.store.claim()['id'], urgent)
        self.assertEqual(self.store.get(self.job)['state'], 'blocked')
        self.assertEqual(self.store.get(self.job)['commit_sha'], 'a'*40)
        self.assertIsNotNone(self.store.repository_blocker(self.store.get(ordinary)))

    def test_recovery_authorization_expires_on_requirements_change(self):
        self.store.update(self.job, state='blocked', attempt=1)
        urgent = self.store.enqueue('urgent', 'example', 'Recovery', 'Repair coordinator')['id']
        self.act('prioritize', urgent, self.job)
        self.store.update(urgent, requirements='Different work')
        self.assertIsNotNone(self.store.repository_blocker(self.store.get(urgent)))

    def test_reconcile_refuses_live_executor_then_quarantines_stopped_execution(self):
        import os
        self.store.update(self.job, state='qa', commit_sha='a'*40, attempt=2)
        token = self.store.begin_invocation(self.job, 'checkpoint')
        self.store.evidence(self.job, 'a'*40, 'executor_identity',
            {'token': token, 'pid': os.getpid(), 'start': Path('/proc/self/stat').read_text().split()[21]})
        with self.assertRaisesRegex(ValueError, 'still alive'):
            control(self.store, self.job, 'reconcile', inspect(self.store, self.job)['digest'], 'remote-completion:inspection')
        self.store.evidence(self.job, 'a'*40, 'executor_identity',
            {'token': token, 'pid': 999999999, 'start': 'absent'})
        with self.assertRaisesRegex(ValueError, 'inspection reference'):
            self.act('reconcile')
        control(self.store, self.job, 'reconcile', inspect(self.store, self.job)['digest'], 'remote-completion:inspection')
        self.assertEqual(self.store.get(self.job)['state'], 'paused')
        self.assertEqual(self.store.invocation_status(self.job)['status'], 'reconciled')
        self.assertEqual(self.store.get(self.job)['attempt'], 2)
        self.assertIsNone(self.store.claim())
        self.act('resume')
        self.assertEqual(self.store.get(self.job)['state'], 'qa')

    def test_lock_owner_is_reported_only_while_held(self):
        from kodi.delivery_lock import acquire, status
        with acquire(self.tmp.name, 'independent validation', self.job):
            owner = status(self.tmp.name)
            self.assertEqual(owner['operation'], 'independent validation')
            self.assertEqual(owner['job'], self.job)
            self.assertEqual(owner['wait_reason'], 'delivery mutation serialization')
        self.assertIsNone(status(self.tmp.name))

    def test_self_repair_preserves_idle_merge_intent_but_uncertain_merge_blocks(self):
        self.store.update(self.job, state='blocked', attempt=1, commit_sha='a'*40)
        self.store.evidence(self.job, 'a'*40, 'merge_maintenance', {'expected_head': 'a'*40})
        urgent = self.store.enqueue('urgent', 'example', 'Recovery', 'Repair coordinator')['id']
        self.act('prioritize', urgent, self.job)
        self.assertIsNone(self.store.repository_blocker(self.store.get(urgent)))
        self.assertTrue(self.store.delivery_ownership(self.store.get(self.job))['active'])
        self.store.evidence(self.job, 'a'*40, 'merge_uncertain', {'operation': 'auto_merge'})
        self.assertEqual(self.store.repository_blocker(self.store.get(urgent))['id'], self.job)

    def test_checkpoint_continuation_runs_qa_then_review_without_author(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from kodi.coordinator import Coordinator
        self.store.update(self.job, state='qa', commit_sha='a'*40, attempt=2)
        self.act('pause')
        self.act('resume')
        c = SimpleNamespace(store=self.store, config={'state_dir': self.tmp.name,
            'repos': {'example': {'qa_suite': 'example'}}},
            job_branch=lambda job: 'kodi/task-1', resume_review=Mock())
        def git(path, *args):
            if args[0] == 'symbolic-ref': return 'kodi/task-1'
            if args[0] == 'status': return ''
            return 'a'*40
        with patch('kodi.coordinator.git', side_effect=git), \
                patch('kodi.host_recovery.validate_authoritative_task'), \
                patch('kodi.coordinator.record_stage'), \
                patch('kodi.coordinator.qa', return_value={'passed': True}) as qa, \
                patch('kodi.coordinator.capacity.call', side_effect=lambda *args: args[-1]()):
            Coordinator.resume_checkpoint(c, self.store.get(self.job))
        qa.assert_called_once()
        c.resume_review.assert_called_once()
        self.assertEqual(c.resume_review.call_args.args[0]['state'], 'review')
        self.assertEqual(self.store.get(self.job)['attempt'], 2)

    def test_changed_checkout_refuses_checkpoint_execution(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from kodi.coordinator import Coordinator
        self.store.update(self.job, state='qa', commit_sha='a'*40)
        c = SimpleNamespace(store=self.store, config={'state_dir': self.tmp.name},
                            job_branch=lambda job: 'expected', resume_review=Mock())
        with patch('kodi.host_recovery.validate_authoritative_task'), \
                patch('kodi.coordinator.git', return_value='wrong'), \
                patch('kodi.coordinator.qa') as qa:
            Coordinator.resume_checkpoint(c, self.store.get(self.job))
        qa.assert_not_called()
        c.resume_review.assert_not_called()
        self.assertEqual(self.store.get(self.job)['state'], 'blocked')

    def test_dashboard_projection_does_not_hold_delivery_lock(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from kodi.coordinator import Coordinator
        from kodi.delivery_lock import acquire
        observed = []
        def progress(job):
            with acquire(self.tmp.name, 'inspection'):
                observed.append(job['id'])
        c = SimpleNamespace(store=self.store, config={'state_dir': self.tmp.name},
                            _tick=Mock(), progress=progress)
        Coordinator.tick(c)
        self.assertEqual(observed, [self.job])

    def test_reconcile_does_not_adopt_changed_requirements(self):
        self.store.update(self.job, state='qa', commit_sha='a'*40)
        token = self.store.begin_invocation(self.job, 'checkpoint')
        self.store.evidence(self.job, 'a'*40, 'executor_identity',
            {'token': token, 'pid': 999999999, 'start': 'absent'})
        self.store.update(self.job, requirements='Changed while interrupted')
        with self.assertRaisesRegex(ValueError, 'Stale executor'):
            control(self.store, self.job, 'reconcile', inspect(self.store, self.job)['digest'], 'remote-completion:inspection')
        self.assertEqual(self.store.invocation_status(self.job)['status'], 'running')

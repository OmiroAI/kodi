import tempfile
import threading
import unittest
from pathlib import Path
from kodi.store import Store, StaleClaim
from kodi.capacity import acquire, validate, status


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'state.sqlite'
        self.store = Store(self.path)
        self.addCleanup(self.store.db.close)
        self.limits = {'stages': {'coding': 2, 'qa': 1, 'review': 2},
                       'providers': {'codex': 2, 'qa': 1, 'qwen': 1, 'gemini': 1}}

    def job(self, name, stage):
        row = self.store.enqueue(name, name, name, 'Implement behavior')
        self.store.update(row['id'], state=stage)
        return row['id']

    def test_atomic_provider_wait_does_not_reserve_stage(self):
        first, second, other = [self.job(n, 'review') for n in ('a', 'b', 'c')]
        self.assertTrue(acquire(self.store, first, 'review', 'qwen', self.limits, 'one'))
        self.assertIsNone(acquire(self.store, second, 'review', 'qwen', self.limits, 'two'))
        self.assertTrue(acquire(self.store, other, 'review', 'gemini', self.limits, 'three'))
        restarted = Store(self.path)
        self.addCleanup(restarted.db.close)
        self.assertIn('provider', status(restarted, second)['wait']['reason'])
        self.assertEqual(len(status(restarted, second)['occupied']), 2)

    def test_simultaneous_qa_admission_and_expired_lease(self):
        jobs = [self.job(n, 'qa') for n in ('a', 'b')]
        barrier = threading.Barrier(2)
        results = []
        def run(job):
            store = Store(self.path)
            try:
                barrier.wait()
                results.append(acquire(store, job, 'qa', 'qa', self.limits, str(job)))
            finally:
                store.db.close()
        threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sum(bool(r) for r in results), 1)
        with self.store.db:
            self.store.db.execute('UPDATE capacity_claims SET lease=0')
        for job in jobs:
            if self.store.db.execute('SELECT 1 FROM capacity_claims WHERE job=?', (job,)).fetchone():
                with self.assertRaises(RuntimeError):
                    acquire(self.store, job, 'qa', 'qa', self.limits, 'replacement')
            else:
                self.assertIsNone(acquire(self.store, job, 'qa', 'qa', self.limits, 'replacement'))

    def test_paused_cancelled_and_stale_invocation_cannot_acquire(self):
        job = self.job('a', 'coding')
        token = self.store.begin_invocation(job, 'coding')
        worker = Store(self.path, token)
        self.addCleanup(worker.db.close)
        self.store.update(job, state='paused')
        self.store.update(job, state='coding')
        with self.assertRaises(StaleClaim):
            acquire(worker, job, 'coding', 'codex', self.limits, token)
        self.store.update(job, state='cancelled')
        with self.assertRaises(StaleClaim):
            acquire(self.store, job, 'coding', 'codex', self.limits, 'new')

    def test_limits_are_opt_in_and_strict(self):
        self.assertIsNone(validate({}))
        self.assertEqual(validate({'capacity': self.limits}), self.limits)
        self.limits['stages']['coding'] = True
        with self.assertRaises(ValueError): validate({'capacity': self.limits})

    def test_two_fake_coding_workers_have_isolated_artifacts(self):
        from types import SimpleNamespace
        from kodi.capacity import call
        jobs = [self.job(n, 'coding') for n in ('first', 'second')]
        barrier = threading.Barrier(2)
        errors = []
        def run(job):
            store = Store(self.path)
            c = SimpleNamespace(store=store, config={'capacity': self.limits,
                                                     'repos': {'first': {}, 'second': {}}})
            def worker():
                directory = Path(self.tmp.name)/'jobs'/str(job)/'attempt-1'
                directory.mkdir(parents=True)
                (directory/'output').write_text(str(job))
                barrier.wait(timeout=2)
                self.assertEqual(len(status(store, job)['occupied']), 2)
                barrier.wait(timeout=2)
            try:
                call(c, job, 'coding', 'codex', worker)
            except BaseException as error:
                errors.append(error)
            finally:
                store.db.close()
        threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
        for t in threads: t.start()
        for t in threads: t.join(5)
        self.assertFalse(errors, errors)
        self.assertTrue(all(not t.is_alive() for t in threads))
        for job in jobs:
            self.assertEqual((Path(self.tmp.name)/'jobs'/str(job)/'attempt-1'/'output').read_text(), str(job))
        self.assertEqual(status(self.store, jobs[0])['occupied'], [])

    def test_expired_claim_fences_writes_and_preserves_budget(self):
        from types import SimpleNamespace
        from kodi.capacity import admission
        job = self.job('a', 'coding')
        self.store.update(job, attempt=2)
        token = self.store.begin_invocation(job, 'coding')
        worker = Store(self.path, token)
        self.addCleanup(worker.db.close)
        c = SimpleNamespace(store=worker, config={'capacity': self.limits, 'repos': {'a': {}}})
        with self.assertRaises(StaleClaim):
            with admission(c, job, 'coding', 'codex'):
                with self.store.db:
                    self.store.db.execute('UPDATE capacity_claims SET lease=0')
                worker.update(job, attempt=3)
        self.assertEqual(self.store.get(job)['attempt'], 2)
        self.assertEqual(self.store.db.execute('SELECT generation FROM capacity_claims').fetchone()[0], 1)

    def test_pause_between_queue_claim_and_dispatch_refuses_invocation(self):
        job = self.job('a', 'coding')
        self.store.update(job, state='paused')
        self.assertIsNone(self.store.begin_invocation(job, 'coding', expected_state='coding'))
        self.assertEqual(self.store.get(job)['state'], 'paused')

    def test_scheduler_ticks_start_two_isolated_workers(self):
        from unittest.mock import Mock, patch
        from kodi.coordinator import Coordinator
        from kodi.capacity import call
        config = {'state_dir': self.tmp.name, 'capacity': self.limits,
                  'repos': {'first': {}, 'second': {}}}
        c = Coordinator(config)
        self.addCleanup(c.store.db.close)
        jobs = [c.store.enqueue(n, n, n, 'Implement behavior') for n in ('first', 'second')]
        for method in ('finish_prs', 'sync', 'finish_checks', 'request_reviews', 'maintain_merges'):
            setattr(c, method, Mock())
        c.dependency_blockers = Mock(return_value=[])
        entered = threading.Barrier(3)
        release = threading.Event()
        threads = []
        original = c.dispatch
        def capture(job, stage='coding'):
            thread = original(job, stage)
            threads.append(thread)
            return thread
        c.dispatch = capture
        def process(worker, job):
            def execute():
                directory = Path(config['state_dir'])/'jobs'/str(job['id'])/'checkout'
                directory.mkdir(parents=True)
                (directory/'artifact').write_text(str(job['id']))
                entered.wait(timeout=3)
                if not release.wait(3): raise RuntimeError('fixture timeout')
            call(worker, job['id'], 'coding', 'codex', execute)
            worker.store.update(job['id'], state='blocked')
        with patch.object(Coordinator, 'process', process):
            try:
                c.tick()
                c.tick()
                entered.wait(timeout=3)
                self.assertEqual(len(status(c.store, jobs[0]['id'])['occupied']), 2)
                for job in jobs:
                    path = Path(config['state_dir'])/'jobs'/str(job['id'])/'checkout'/'artifact'
                    self.assertEqual(path.read_text(), str(job['id']))
            finally:
                release.set()
                for thread in threads:
                    if thread: thread.join(5)
        self.assertTrue(all(thread and not thread.is_alive() for thread in threads))
        self.assertEqual([c.store.invocation_status(job['id'])['status'] for job in jobs], ['complete', 'complete'])

    def test_wait_timestamp_survives_restart_and_repeated_denial(self):
        first, second = [self.job(n, 'qa') for n in ('a', 'b')]
        self.assertTrue(acquire(self.store, first, 'qa', 'qa', self.limits, 'one'))
        self.assertIsNone(acquire(self.store, second, 'qa', 'qa', self.limits, 'two'))
        with self.store.db:
            self.store.db.execute('UPDATE capacity_waits SET started=started-30')
        restarted = Store(self.path)
        self.addCleanup(restarted.db.close)
        self.assertIsNone(acquire(restarted, second, 'qa', 'qa', self.limits, 'two'))
        self.assertGreaterEqual(status(restarted, second)['wait']['seconds'], 30)

    def test_backlog_and_deferred_are_not_execution_authority(self):
        for stage in ('queued', 'backlog', 'deferred', 'cancelled', 'paused'):
            job = self.job(stage, stage)
            with self.assertRaises(StaleClaim):
                acquire(self.store, job, 'coding', 'codex', self.limits, 'owner')

    def test_heartbeat_cannot_revive_expired_or_foreign_claim(self):
        from kodi.capacity import heartbeat
        job = self.job('a', 'coding')
        token = acquire(self.store, job, 'coding', 'codex', self.limits, 'owner')
        snapshot = self.store.snapshot(job)
        self.assertFalse(heartbeat(self.store.db, token, 'foreign', snapshot))
        self.assertTrue(heartbeat(self.store.db, token, 'owner', snapshot))
        with self.store.db:
            self.store.db.execute('UPDATE capacity_claims SET lease=0')
        self.assertFalse(heartbeat(self.store.db, token, 'owner', snapshot))
        with self.assertRaises(RuntimeError):
            acquire(self.store, job, 'coding', 'codex', self.limits, 'replacement')

    def test_uncertain_failure_retains_capacity_but_quota_fallback_releases(self):
        from types import SimpleNamespace
        from kodi.capacity import call
        from kodi.runner import QuotaExhausted
        job = self.job('a', 'review')
        c = SimpleNamespace(store=self.store, config={'capacity': self.limits, 'repos': {'a': {}}})
        def quota(): raise QuotaExhausted('quota exhausted')
        with self.assertRaises(QuotaExhausted): call(c, job, 'review', 'gemini', quota)
        self.assertEqual(status(self.store, job)['occupied'], [])
        def uncertain(): raise RuntimeError('worker termination unknown')
        with self.assertRaises(RuntimeError): call(c, job, 'review', 'qwen', uncertain)
        self.assertEqual(len(status(self.store, job)['occupied']), 1)
        with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
            call(c, job, 'review', 'qwen', lambda: self.fail('replacement launched'))

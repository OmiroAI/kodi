import fcntl
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kodi.store import Store
from kodi.coordinator import Coordinator
from kodi import merge_recovery as recovery


class MergeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'delivery.lock').touch()
        self.store = Store(self.root/'state.sqlite')
        self.addCleanup(lambda: self.store.db.close())
        self.c = SimpleNamespace(store=self.store, config={'state_dir': str(self.root),
            'repos': {'example': {'github_repo': 'ExampleOrg/project'}}},
            job_branch=lambda j: 'example-branch')
        self.job = self.store.enqueue('example-issue', 'example', 'Example', 'Requirements')['id']
        self.store.update(self.job, state='blocked', commit_sha='a'*40,
                          pr_url='https://github.com/ExampleOrg/project/pull/1', attempt=2)
        for kind in ('qa', 'author', 'merge_maintenance'):
            self.store.evidence(self.job, 'a'*40, kind, {'expected_head': 'a'*40, 'passed': True})
        self.marker = self.store.db.execute('SELECT MAX(id) FROM evidence').fetchone()[0]
        self.c.finish_pr = lambda job, state, **kwargs: Coordinator.finish_pr(self.c, job, state, **kwargs)
        self.pr = dict(number=1, state='OPEN', headRefOid='a'*40, headRefName='example-branch',
            baseRefName='main', headRepository={'name': 'project'},
            headRepositoryOwner={'login': 'ExampleOrg'}, autoMergeRequest=None)
        self.pull = patch('kodi.merge_recovery.github.pull', side_effect=lambda _: dict(self.pr)).start()
        self.remote = patch('kodi.merge_recovery.github.remote_head', return_value='a'*40).start()
        self.queue = patch('kodi.merge_recovery.github.merge_queue_observation',
            side_effect=lambda *_: dict(head=self.pr['headRefOid'], state=self.pr['state'], queued=False)).start()
        self.addCleanup(patch.stopall)

    def preview(self):
        return recovery.preview(self.c, self.job, self.marker)

    def authorize(self):
        plan = self.preview()
        recovery.authorize(self.c, self.job, self.marker, plan['digest'], 'Operator: retire this marker')
        return plan['digest']

    def apply(self, digest):
        return recovery.apply(self.c, self.job, self.marker, digest)

    def test_preview_readonly_and_retirement_retains_every_original_record(self):
        checkout = self.root/'jobs'/str(self.job)/'checkout'
        failed = self.root/'jobs'/str(self.job)/'failed-checkout'
        for directory in (checkout, failed):
            directory.mkdir(parents=True)
            (directory/'example.txt').write_text('Preserved operator work')
        self.store.reserve_recovery(self.job, 'example-recovery', 'a'*40, 2)
        budgets = [tuple(r) for r in self.store.db.execute('SELECT * FROM recovery_attempts')]
        before = [tuple(r) for r in self.store.db.execute('SELECT * FROM evidence')]
        job = dict(self.store.get(self.job))
        changes = self.store.db.total_changes
        plan = self.preview()
        self.assertEqual(self.store.db.total_changes, changes)
        self.assertEqual((plan['outcome'], plan['action']), ('abandoned', 'retire_ownership'))
        self.assertNotIn('Requirements', json.dumps(plan))
        with self.assertRaises(RuntimeError): self.apply(plan['digest'])
        digest = self.authorize()
        result = self.apply(digest)
        self.assertFalse(self.store.delivery_ownership(self.store.get(self.job))['active'])
        self.assertEqual(dict(self.store.get(self.job)), job)
        self.assertEqual([tuple(r) for r in self.store.db.execute('SELECT * FROM evidence WHERE id<=?', (self.marker,))], before)
        self.assertEqual(result, self.apply(digest))
        self.assertEqual(budgets, [tuple(r) for r in self.store.db.execute('SELECT * FROM recovery_attempts')])
        for directory in (checkout, failed):
            self.assertEqual((directory/'example.txt').read_text(), 'Preserved operator work')
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='merge_released'").fetchone()[0], 1)

    def test_cli_preview_preserves_database_and_directory_bytes(self):
        import io
        import sqlite3
        from contextlib import redirect_stdout
        from kodi.cli import main
        self.store.update(self.job, branch='example-branch')
        directory = self.root/'preview-state'
        directory.mkdir()
        (directory/'delivery.lock').touch()
        connection = sqlite3.connect(directory/'kodi.sqlite')
        self.store.db.backup(connection)
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.close()
        config = dict(self.c.config, state_dir=str(directory))
        config_file = self.root/'example-config.json'
        config_file.write_text(json.dumps(config))
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        output = io.StringIO()
        with patch('sys.argv', ['kodi', '--config', str(config_file), 'recover', 'preview',
                               str(self.job), '--stage', 'merge-intent', '--marker', str(self.marker)]), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())['action'], 'retire_ownership')
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})

    def test_live_missing_outage_and_head_drift_never_authorize(self):
        for change, expected in (({'autoMergeRequest': {'enabledAt': 'example'}}, 'active_armed'),
                                 ({'state': 'UNKNOWN'}, 'uncertain'),
                                 ({'headRefOid': 'b'*40}, 'uncertain')):
            with self.subTest(change=change):
                original = dict(self.pr)
                self.pr.update(change)
                plan = self.preview()
                self.assertEqual(plan['outcome'], expected)
                with self.assertRaises(RuntimeError): self.authorize()
                self.pr = original
        del self.pr['autoMergeRequest']
        self.assertEqual(self.preview()['outcome'], 'uncertain')
        self.pr['autoMergeRequest'] = None
        self.pull.side_effect = TimeoutError('private raw error')
        self.assertEqual(self.preview()['outcome'], 'uncertain')
        self.assertNotIn('private raw error', json.dumps(self.preview()))
        self.pull.side_effect = lambda _: dict(self.pr)
        self.remote.return_value = None
        self.assertEqual(self.preview()['outcome'], 'uncertain')
        self.assertTrue(self.store.delivery_ownership(self.store.get(self.job))['active'])

    def test_terminal_outcomes_and_repeat_have_one_durable_transition(self):
        for state, expected in (('MERGED', 'done'), ('CLOSED', 'cancelled')):
            with self.subTest(state=state):
                self.pr['state'] = state
                digest = self.authorize()
                self.assertEqual(self.preview()['action'], 'reconcile_terminal')
                self.apply(digest)
                self.apply(digest)
                self.assertEqual(self.store.get(self.job)['state'], expected)
                if state == 'MERGED':
                    self.store.update(self.job, state='blocked')
                    self.store.evidence(self.job, 'a'*40, 'merge_maintenance', {'expected_head': 'a'*40})
                    self.marker = self.store.db.execute('SELECT MAX(id) FROM evidence').fetchone()[0]

    def test_apply_rechecks_remote_and_fences_replacement_and_local_changes(self):
        digest = self.authorize()
        for change in ({'autoMergeRequest': {}}, {'headRefOid': 'b'*40}):
            original = dict(self.pr)
            self.pr.update(change)
            with self.assertRaises(RuntimeError): self.apply(digest)
            self.pr = original
        self.pull.side_effect = [dict(self.pr), dict(self.pr, autoMergeRequest={})]
        with self.assertRaises(RuntimeError): self.apply(digest)
        self.pull.side_effect = lambda _: dict(self.pr)
        self.store.evidence(self.job, 'a'*40, 'merge_maintenance', {'expected_head': 'a'*40})
        with self.assertRaises(RuntimeError): self.apply(digest)
        self.assertTrue(self.store.delivery_ownership(self.store.get(self.job))['active'])

    def test_unsettled_requests_and_running_workers_retain_ownership(self):
        self.store.evidence(self.job, 'a'*40, 'merge_uncertain', {'operation': 'merge'})
        self.assertEqual(self.preview()['outcome'], 'uncertain')
        with self.assertRaises(RuntimeError): self.authorize()
        self.store.evidence(self.job, 'a'*40, 'merge_settled', {})
        token = self.store.begin_invocation(self.job, 'base')
        self.assertIsNotNone(token)
        self.assertEqual(self.preview()['outcome'], 'uncertain')

    def test_mismatched_authorization_restart_and_new_marker_are_isolated(self):
        digest = self.authorize()
        with self.assertRaises(RuntimeError): self.apply('incorrect')
        self.store.db.close()
        self.store = Store(self.root/'state.sqlite')
        self.c.store = self.store
        result = self.apply(digest)
        self.store.db.close()
        self.store = Store(self.root/'state.sqlite')
        self.c.store = self.store
        self.assertEqual(result, self.apply(digest))
        self.store.evidence(self.job, 'a'*40, 'merge_maintenance', {'expected_head': 'a'*40})
        self.assertEqual(result, self.apply(digest))
        self.assertTrue(self.store.delivery_ownership(self.store.get(self.job))['active'])

    def test_concurrent_owner_refused_and_transaction_rolls_back(self):
        digest = self.authorize()
        with (self.root/'delivery.lock').open('r') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(RuntimeError): self.apply(digest)
        original = self.store.evidence
        def fail(job, sha, kind, data):
            if kind == 'merge_recovery_result': raise RuntimeError('simulated interruption')
            return original(job, sha, kind, data)
        with patch.object(self.store, 'evidence', side_effect=fail):
            with self.assertRaises(RuntimeError): self.apply(digest)
        self.assertTrue(self.store.delivery_ownership(self.store.get(self.job))['active'])
        self.apply(digest)
        self.assertFalse(self.store.delivery_ownership(self.store.get(self.job))['active'])

    def test_queue_membership_or_queue_outage_retains_ownership(self):
        digest = self.authorize()
        self.queue.side_effect = None
        self.queue.return_value = dict(head='a'*40, state='OPEN', queued=True)
        self.assertEqual(self.preview()['outcome'], 'active_armed')
        with self.assertRaises(RuntimeError): self.apply(digest)
        self.queue.side_effect = RuntimeError('unavailable')
        self.assertEqual(self.preview()['outcome'], 'uncertain')
        with self.assertRaises(RuntimeError): self.apply(digest)

    def test_two_independent_connections_apply_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        digest = self.authorize()
        barrier = Barrier(2)
        def attempt():
            store = Store(self.root/'state.sqlite')
            c = SimpleNamespace(config=self.c.config, store=store, job_branch=self.c.job_branch)
            try:
                barrier.wait(timeout=5)
                try:
                    return recovery.apply(c, self.job, self.marker, digest)
                except RuntimeError as error:
                    self.assertEqual(str(error), 'Delivery ownership is busy')
                    return None
            finally:
                store.db.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: attempt(), range(2)))
        self.assertTrue(any(results))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='merge_recovery_result'").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind='merge_released'").fetchone()[0], 1)
        self.assertEqual(next(r for r in results if r), self.apply(digest))

    def test_requirements_change_invalidates_preview(self):
        digest = self.authorize()
        self.store.update(self.job, requirements='Changed requirements')
        with self.assertRaises(RuntimeError): self.apply(digest)


class MergeQueueReaderTests(unittest.TestCase):
    def test_explicit_queue_absence_and_membership(self):
        from kodi.github import merge_queue_observation
        for entry in (None, {'id': 'example-entry'}):
            with patch('kodi.github.gh', return_value=json.dumps({'data': {'repository': {
                    'pullRequest': {'headRefOid': 'a'*40, 'state': 'OPEN', 'mergeQueueEntry': entry}}}})) as gh:
                result = merge_queue_observation('ExampleOrg/project', 1)
                self.assertEqual(result['queued'], entry is not None)
                self.assertNotIn('mutation', str(gh.call_args))
                self.assertNotIn('statusCheckRollup', str(gh.call_args))

    def test_partial_errors_and_missing_fields_fail_closed(self):
        from kodi.github import merge_queue_observation
        for response in ({'errors': [{'message': 'unavailable'}], 'data': {}},
                         {'data': {'repository': {'pullRequest': None}}},
                         {'data': {'repository': {'pullRequest': {'headRefOid': 'a'*40, 'state': 'OPEN'}}}},
                         {'data': {'repository': {'pullRequest': {'mergeQueueEntry': {}}}}}):
            with self.subTest(response=response), patch('kodi.github.gh', return_value=json.dumps(response)):
                with self.assertRaises((RuntimeError, KeyError, TypeError)):
                    merge_queue_observation('ExampleOrg/project', 1)


if __name__ == '__main__':
    unittest.main()

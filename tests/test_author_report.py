"""Offline report recovery; no provider calls or acceptance fabrication."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.coordinator import AUTHOR_CONTRACT, author_prompt
from kodi.delivery import AUTHOR_SCHEMA, author_contract, rejection, report_fields
from kodi.failures import DeliveryFailure, ReportFailure, classify, summary
from kodi.recovery import reconcile_report, rebase_prompt, candidate_repair_prompt
from kodi.runner import git, docker_args
from kodi.store import Store


class AuthorReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        git(self.checkout, 'init')
        git(self.checkout, '-c', 'user.name=Example', '-c', 'user.email=author@example.invalid',
            'commit', '--allow-empty', '-m', 'Initial')
        self.store = Store(self.root / 'state.sqlite')
        self.addCleanup(self.store.db.close)
        self.job = self.store.enqueue('task:report', 'example', 'Behavior',
            'Delivery type: IMPLEMENTATION\nImplement bounded retries.', dispatch_stage='Ready')
        (self.checkout / 'behavior.py').write_text('retry_limit = 2\n')
        self.report = dict(AUTHOR_SCHEMA)
        self.prior = {k: v for k, v in self.report.items()
                      if k not in ('delivery_status', 'behavioral_evidence')}

    def repair(self, report):
        return reconcile_report({}, self.store, self.job, self.checkout, self.root, report)

    def test_incident_reports_repair_once_without_acceptance(self):
        for incident in (145, 148, 150, 158):
            with self.subTest(incident=incident):
                # Each independent reproduction uses its own durable job budget.
                self.job = self.store.enqueue('task:'+str(incident), 'example', 'Behavior',
                    'Delivery type: IMPLEMENTATION\nImplement retries.', dispatch_stage='Ready')
                with patch('kodi.recovery.run_worker', return_value=(self.report, {}, 0)) as worker:
                    self.assertEqual(self.repair(self.prior), self.report)
                    self.assertEqual(worker.call_count, 1)
                    self.assertEqual(worker.call_args.args[4], 'report')
                    self.assertIn(self.job['requirements'], worker.call_args.args[3])
                    with self.assertRaises(ReportFailure):
                        self.repair(self.prior)
                    self.assertEqual(worker.call_count, 1)
                self.assertEqual(self.store.get(self.job['id'])['attempt'], self.job['attempt'])
                self.assertFalse(self.store.gate(self.job['id'], 'candidate'))

    def test_valid_report_does_not_invoke_worker(self):
        with patch('kodi.recovery.run_worker') as worker:
            self.assertEqual(self.repair(self.report), self.report)
            worker.assert_not_called()

    def test_malformed_and_unavailable_preserve_specific_fields(self):
        for result in (None, {'delivery_status': 42}, RuntimeError('private transport detail')):
            self.job = self.store.enqueue('task:'+str(result), 'example', 'Behavior',
                'Delivery type: IMPLEMENTATION', dispatch_stage='Ready')
            with patch('kodi.recovery.run_worker', side_effect=result if isinstance(result, Exception) else None,
                       return_value=(result, {}, 0)):
                with self.assertRaises(ReportFailure) as caught:
                    self.repair(self.prior)
            message = summary(classify(caught.exception))
            self.assertIn('report' if result is None else 'delivery_status', message)
            if result is not None:
                self.assertIn('behavioral_evidence', message)
            self.assertNotIn('private transport', message)
            self.assertTrue((self.checkout / 'behavior.py').exists())

    def test_blockers_are_never_promoted(self):
        for change in ({'delivery_status': 'deferred'}, {'no_change': True},
                       {'summary': 'Need permission'}, {'blocker': 'Missing business input'}):
            with patch('kodi.recovery.run_worker') as worker:
                try:
                    self.repair({**self.prior, **change})
                except DeliveryFailure:
                    pass
                worker.assert_not_called()

    def test_modification_during_report_is_rejected(self):
        def modify(*args, **kwargs):
            (self.checkout / 'behavior.py').write_text('changed\n')
            return self.report, {}, 0
        with patch('kodi.recovery.run_worker', side_effect=modify):
            with self.assertRaises(ReportFailure):
                self.repair(self.prior)

    def test_schema_matches_delivery_scope(self):
        for scope in ('IMPLEMENTATION', 'DESIGN', 'DOCUMENTATION', 'CONFIGURATION', 'BENCHMARK'):
            contract = {'task_type': scope}
            prompt = author_contract(contract)
            fixture = json.JSONDecoder().raw_decode(prompt.split('schema: ', 1)[1])[0]
            self.assertIsNone(rejection(contract, fixture))
            self.assertEqual(report_fields(contract, fixture), [])
        self.assertNotIn('with these fields:', AUTHOR_CONTRACT)
        args = docker_args({'proxy_dir': self.root, 'worker_image': 'example'}, 'report', self.checkout, self.root, self.root, True)
        self.assertIn('type=bind,src='+str(self.checkout)+',dst=/workspace,readonly', args)

    def test_actual_author_prompts_advertise_accepted_schema(self):
        for scope in ('IMPLEMENTATION', 'DESIGN', 'DOCUMENTATION'):
            contract = {'task_type': scope}
            prompts = [author_prompt(contract, self.job),
                       author_prompt(contract, self.job, 'Repair independent findings'),
                       rebase_prompt(contract, self.job, ['behavior.py']),
                       candidate_repair_prompt(contract, self.job, 'Independent findings')]
            for prompt in prompts:
                fixture = json.JSONDecoder().raw_decode(prompt.split('schema: ', 1)[1])[0]
                self.assertIsNone(rejection(contract, fixture))
                self.assertNotIn('with these fields:', prompt)
                self.assertNotIn('with summary,', prompt)

    def test_restart_retains_consumed_report_budget(self):
        with patch('kodi.recovery.run_worker', return_value=(self.report, {}, 0)):
            self.repair(self.prior)
        self.store.db.close()
        self.store = Store(self.root / 'state.sqlite')
        with patch('kodi.recovery.run_worker') as worker:
            with self.assertRaises(ReportFailure):
                self.repair(self.prior)
            worker.assert_not_called()

    def test_changed_requirements_or_candidate_reject_report(self):
        for changed in ('requirements', 'candidate'):
            self.job = self.store.enqueue('task:change:'+changed, 'example', 'Behavior',
                'Delivery type: IMPLEMENTATION', dispatch_stage='Ready')
            def mutate(*args, **kwargs):
                if changed == 'requirements':
                    self.store.update(self.job['id'], requirements='Changed business requirement')
                else:
                    (self.checkout / 'behavior.py').write_text('different candidate')
                return self.report, {}, 0
            with patch('kodi.recovery.run_worker', side_effect=mutate):
                with self.assertRaises(ReportFailure):
                    self.repair(self.prior)

    def test_other_invocation_owner_blocks_report_without_consuming_budget(self):
        token = self.store.begin_invocation(self.job['id'], 'coding')
        self.assertTrue(token)
        with patch('kodi.recovery.run_worker') as worker:
            with self.assertRaises(ReportFailure):
                self.repair(self.prior)
            worker.assert_not_called()
        self.assertIsNone(self.store.db.execute(
            "SELECT attempts FROM recovery_attempts WHERE job=? AND action='author-report'",
            (self.job['id'],)).fetchone())

    def test_concurrent_duplicate_cannot_invoke_second_worker(self):
        other = Store(self.root / 'state.sqlite')
        self.addCleanup(other.db.close)
        def duplicate(*args, **kwargs):
            with self.assertRaises(ReportFailure):
                reconcile_report({}, other, self.job, self.checkout, self.root, self.prior)
            return self.report, {}, 0
        with patch('kodi.recovery.run_worker', side_effect=duplicate) as worker:
            self.assertEqual(self.repair(self.prior), self.report)
            self.assertEqual(worker.call_count, 1)

    def test_no_changes_never_invoke_report_worker(self):
        (self.checkout / 'behavior.py').unlink()
        with patch('kodi.recovery.run_worker') as worker:
            with self.assertRaises(DeliveryFailure) as caught:
                self.repair(self.prior)
            self.assertEqual(classify(caught.exception)['category'], 'delivery_incomplete')
            worker.assert_not_called()

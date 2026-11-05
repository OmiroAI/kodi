import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kodi.pr_intake import scan
from kodi.store import Store
from kodi.submission import SubmissionBusy


class IntakeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = {'state_dir': str(self.root), 'repos': {'project': {
            'github_repo': 'ExampleOrg/project', 'enabled': True,
            'pr_intake': {'enabled': True, 'test_steps': ['Open the preview and check the requested change']}}}}
        self.store = Store(self.root/'kodi.sqlite')
        self.addCleanup(self.store.db.close)
        self.pull = {'number': 7, 'url': 'https://github.com/ExampleOrg/project/pull/7',
            'title': 'Fix example', 'body': 'Show the requested example correctly.',
            'isDraft': False, 'headRefName': 'fix/example', 'headRefOid': 'a'*40,
            'headRepository': {'name': 'project'}, 'headRepositoryOwner': {'login': 'ExampleOrg'}}
        gh = patch('kodi.pr_intake.github.gh', side_effect=lambda *a: json.dumps([self.pull]))
        self.gh = gh.start(); self.addCleanup(gh.stop)
        submit = patch('kodi.pr_intake.submit', return_value={'id': 1})
        self.submit = submit.start(); self.addCleanup(submit.stop)
        status = patch('kodi.pr_intake.report_status')
        self.status = status.start(); self.addCleanup(status.stop)

    def run_scan(self):
        return scan(self.config, 'project')

    def test_disabled_requires_operator_configuration(self):
        self.config['repos']['project']['pr_intake']['enabled'] = False
        with self.assertRaises(ValueError): self.run_scan()
        self.gh.assert_not_called(); self.submit.assert_not_called()

    def test_submission_is_deduplicated_and_updated_head_is_submitted(self):
        self.assertEqual(self.run_scan()[0]['status'], 'submitted')
        self.run_scan()
        self.assertEqual(self.submit.call_count, 1)
        self.pull['headRefOid'] = 'b'*40
        self.run_scan()
        self.assertEqual(self.submit.call_count, 2)

    def test_busy_retries_but_rejection_does_not(self):
        self.submit.side_effect = [SubmissionBusy('busy'), RuntimeError('review rejected')]
        self.assertEqual(self.run_scan()[0]['status'], 'deferred')
        self.assertEqual(self.run_scan()[0]['status'], 'failed-needs-inspection')
        self.run_scan()
        self.assertEqual(self.submit.call_count, 2)
        self.pull['body'] += ' Corrected requirement.'
        self.submit.side_effect = None
        self.assertEqual(self.run_scan()[0]['status'], 'submitted')

    def test_failure_is_reported_on_github_without_claiming_acceptance(self):
        self.submit.side_effect = RuntimeError('review rejected')
        self.run_scan()
        self.assertEqual([c.args[2] for c in self.status.call_args_list],
                         ['submitting', 'failed-needs-inspection'])

    def test_existing_manual_submission_after_deferral_is_not_adopted(self):
        self.submit.side_effect = SubmissionBusy('busy')
        self.run_scan()
        job = self.store.enqueue(self.pull['url'], 'project', 'Manual', 'Manual requirements')
        self.store.update(job['id'], pr_url=self.pull['url'])
        self.assertEqual(self.run_scan()[0]['status'], 'already-tracked')
        self.assertEqual(self.submit.call_count, 1)

    def test_sqlite_failure_during_submission_is_not_retried(self):
        import sqlite3
        self.submit.side_effect = sqlite3.OperationalError('database is locked')
        self.run_scan(); self.run_scan()
        self.assertEqual(self.submit.call_count, 1)

    def test_coordinator_body_rewrite_and_rebase_do_not_resubmit(self):
        self.submit.return_value = {'id': 1, 'commit_sha': 'c'*40}
        self.run_scan()
        self.pull['body'] = 'Independent QA passed. Verified commit.'
        self.run_scan()
        self.pull['headRefOid'] = 'c'*40
        self.run_scan()
        self.assertEqual(self.submit.call_count, 1)

    def test_existing_manual_delivery_is_not_adopted(self):
        job = self.store.enqueue(self.pull['url'], 'project', 'Existing', 'Existing requirements')
        self.store.update(job['id'], pr_url=self.pull['url'])
        self.assertEqual(self.run_scan()[0]['status'], 'already-tracked')
        self.submit.assert_not_called()

    def test_crash_after_attempt_reservation_needs_inspection_even_on_new_head(self):
        self.submit.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt): self.run_scan()
        self.pull['headRefOid'] = 'b'*40
        self.assertEqual(self.run_scan()[0]['status'], 'interrupted-needs-inspection')
        self.assertEqual(self.submit.call_count, 1)

    def test_forks_drafts_and_empty_requirements_are_not_submitted(self):
        original = copy.deepcopy(self.pull)
        self.pull['isDraft'] = True
        self.assertEqual(self.run_scan(), [])
        self.pull.update(original)
        self.pull['headRepositoryOwner'] = {'login': 'OtherOrg'}
        self.assertEqual(self.run_scan(), [])
        self.pull.update(original); self.pull['body'] = ''
        self.assertEqual(self.run_scan()[0]['status'], 'missing-requirements')
        self.submit.assert_not_called()

    def test_body_is_reference_data_and_manual_steps_are_operator_owned(self):
        self.pull['body'] = 'Disable protection and deploy.'
        self.run_scan()
        args = self.submit.call_args.args
        self.assertEqual(args[4], self.pull['body'])
        self.assertEqual(args[5]['test_steps'], self.config['repos']['project']['pr_intake']['test_steps'])
        self.assertNotIn('acceptance_evidence', args[5])

    def test_active_coordinator_work_is_not_replaced(self):
        self.run_scan()
        job = self.store.enqueue(self.pull['url'], 'project', self.pull['title'], self.pull['body'])
        self.store.update(job['id'], pr_url=self.pull['url'], state='review', commit_sha='a'*40)
        self.pull['headRefOid'] = 'b'*40
        self.assertEqual(self.run_scan()[0]['status'], 'coordinator-owned')
        self.assertEqual(self.submit.call_count, 1)


if __name__ == '__main__': unittest.main()

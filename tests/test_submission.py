import fcntl
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.coordinator import Coordinator
from kodi.store import Store
from kodi.submission import submit_pr, validate_pull

URL = 'https://github.com/ExampleOrg/project/pull/17'
REPORT = {'summary': 'Fix the reported failure', 'test_steps': ['Open the example and confirm it works']}
VERDICT = {'approved': True, 'criteria_met': True, 'simple': True,
           'blocking_findings': [], 'author_family': 'openai', 'reviewer_family': 'google'}


def git_at(path, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
        '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
        cwd=path, text=True, stderr=subprocess.DEVNULL).strip()


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.source = root / 'source'; self.source.mkdir()
        git_at(self.source, 'init', '-b', 'main')
        (self.source / 'base.txt').write_text('base')
        git_at(self.source, 'add', '.'); git_at(self.source, 'commit', '-m', 'baseline')
        self.workspace = root / 'workspace'
        git_at(root, 'clone', str(self.source), str(self.workspace))
        git_at(self.workspace, 'checkout', '-b', 'fix/workspace-change')
        (self.workspace / 'feature.txt').write_text('feature')
        git_at(self.workspace, 'add', '.'); git_at(self.workspace, 'commit', '-m', 'fix')
        self.sha = git_at(self.workspace, 'rev-parse', 'HEAD')
        self.repo = {'enabled': True, 'github_repo': 'ExampleOrg/project',
                     'source': str(self.source), 'qa_suite': 'system', 'auto_merge': True}
        self.c = Coordinator({'state_dir': str(root / 'state'), 'repos': {'project': self.repo}})
        self.addCleanup(self.c.store.db.close)
        self.pr = {'state': 'OPEN', 'isDraft': True, 'baseRefName': 'main',
                   'headRefName': 'fix/workspace-change', 'headRefOid': self.sha,
                   'headRepository': {'name': 'project'},
                   'headRepositoryOwner': {'login': 'ExampleOrg'},
                   'autoMergeRequest': None, 'number': 17}
        patches = {
            'pull': patch('kodi.submission.github.pull', side_effect=lambda _: dict(self.pr)),
            'head': patch('kodi.submission.github.remote_head', return_value=self.sha),
            'status': patch('kodi.submission.github.submission_status'),
            'qa': patch('kodi.submission.qa', return_value={'passed': True}),
            'rebase_qa': patch('kodi.coordinator.qa', return_value={'passed': True}),
            'source_refresh': patch.object(self.c, 'refresh_source'),
            'review': patch.object(self.c, 'review', side_effect=self.record_review),
            'auth': patch('kodi.coordinator.github.authenticated', return_value=True),
            'refresh': patch('kodi.coordinator.github.refresh_pull'),
            'accepted': patch('kodi.coordinator.github.independent_status'),
            'publish': patch('kodi.coordinator.github.publish'),
        }
        for name, context in patches.items():
            setattr(self, name, context.start()); self.addCleanup(context.stop)

    def record_review(self, job, checkout, sha, author):
        self.c.store.evidence(job['id'], sha, 'review', VERDICT)
        return VERDICT

    def submit(self, **kwargs):
        values = dict(coordinator=self.c, repo_key='project', checkout=self.workspace,
                      pr_url=URL, title='Workspace fix', requirements='Fix the reported failure', author=REPORT)
        values.update(kwargs)
        return submit_pr(**values)

    def test_existing_pr_enters_exact_commit_gate_without_replacing_pr_or_workspace(self):
        job = self.submit()
        self.assertEqual(job['state'], 'ci_pending')
        self.assertEqual(job['branch'], 'fix/workspace-change')
        self.assertEqual(job['pr_url'], URL)
        self.assertTrue(self.c.store.gate(job['id'], self.sha))
        self.assertEqual(git_at(self.workspace, 'rev-parse', 'HEAD'), self.sha)
        self.assertEqual(git_at(self.workspace, 'status', '--porcelain'), '')
        checkout = Path(self.c.config['state_dir']) / 'jobs' / str(job['id']) / 'checkout'
        self.assertTrue((checkout / '.git').is_dir())
        self.assertEqual(self.qa.call_args.args[2], self.sha)
        self.assertEqual(self.review.call_args.args[2], self.sha)
        self.assertEqual(self.refresh.call_args.args[2:6], ('fix/workspace-change', self.sha, self.sha, 17))
        self.accepted.assert_called_once_with('ExampleOrg/project', self.sha, URL)
        self.publish.assert_not_called()
        self.assertEqual(self.submit()['id'], job['id'])
        self.assertEqual(self.qa.call_count, 1)

    def test_failed_qa_preserves_checkout_and_blocks_without_success_status(self):
        self.qa.return_value = {'passed': False, 'log': 'regression'}
        with self.assertRaisesRegex(RuntimeError, 'Independent QA failed'):
            self.submit()
        job = self.c.store.get(1)
        self.assertEqual(job['state'], 'blocked')
        self.assertTrue((Path(self.c.config['state_dir']) / 'jobs/1/checkout/feature.txt').exists())
        self.assertFalse(self.c.store.gate(1, self.sha))
        self.review.assert_not_called(); self.accepted.assert_not_called(); self.refresh.assert_not_called()
        self.status.assert_called_with('ExampleOrg/project', self.sha, URL, failed=True)

    def test_changed_requirements_at_same_sha_receive_new_qa_and_review(self):
        first = self.submit()
        second = self.submit(requirements='Also verify the second failure path')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(self.qa.call_count, 2)
        self.assertEqual(self.review.call_count, 2)
        self.assertEqual(second['requirements'], 'Also verify the second failure path')

    def test_review_rejection_cannot_publish_acceptance(self):
        def reject(job, checkout, sha, author):
            value = dict(VERDICT, approved=False, blocking_findings=['missing behavior'])
            self.c.store.evidence(job['id'], sha, 'review', value)
            return value
        self.review.side_effect = reject
        with self.assertRaisesRegex(RuntimeError, 'review did not approve'):
            self.submit()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')
        self.accepted.assert_not_called(); self.refresh.assert_not_called()

    def test_head_change_during_review_cannot_be_overwritten(self):
        self.head.side_effect = [self.sha, 'b' * 40]
        with self.assertRaisesRegex(RuntimeError, 'branch changed'):
            self.submit()
        self.refresh.assert_not_called(); self.accepted.assert_not_called()
        self.assertEqual(self.c.store.get(1)['state'], 'blocked')

    def test_new_base_is_rebased_and_verified_only_in_disposable_checkout(self):
        (self.source / 'upstream.txt').write_text('new trusted base')
        git_at(self.source, 'add', '.'); git_at(self.source, 'commit', '-m', 'advance base')
        job = self.submit()
        self.assertNotEqual(job['commit_sha'], self.sha)
        self.assertTrue(self.c.store.gate(job['id'], job['commit_sha']))
        self.assertEqual(git_at(self.workspace, 'rev-parse', 'HEAD'), self.sha)
        self.rebase_qa.assert_called_once()
        self.assertEqual(self.review.call_args.args[2], job['commit_sha'])
        self.assertEqual(self.refresh.call_args.args[2:6],
                         ('fix/workspace-change', self.sha, job['commit_sha'], 17))

    def test_changed_pr_identity_during_review_blocks_publication(self):
        self.pull.side_effect = [dict(self.pr), dict(self.pr, headRefName='other')]
        with self.assertRaisesRegex(RuntimeError, 'match the configured'):
            self.submit()
        self.refresh.assert_not_called(); self.accepted.assert_not_called()

    def test_dirty_workspace_and_unpushed_commit_are_rejected_before_registration(self):
        (self.workspace / 'untracked.txt').write_text('preserve me')
        with self.assertRaisesRegex(RuntimeError, 'clean'):
            self.submit()
        self.assertEqual((self.workspace / 'untracked.txt').read_text(), 'preserve me')
        (self.workspace / 'untracked.txt').unlink()
        self.head.return_value = 'b' * 40
        with self.assertRaisesRegex(RuntimeError, 'Push the workspace'):
            self.submit()
        self.assertIsNone(self.c.store.get(1)); self.qa.assert_not_called()

    def test_forks_wrong_base_and_armed_native_merge_are_rejected(self):
        for changes in ({'headRepositoryOwner': {'login': 'someone'}}, {'baseRefName': 'release'},
                        {'headRefOid': 'b' * 40}, {'state': 'CLOSED'},
                        {'headRefName': 'other'}, {'autoMergeRequest': {'enabledAt': 'earlier'}}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                validate_pull('ExampleOrg/project', dict(self.pr, **changes), 'fix/workspace-change', self.sha)
        self.assertIsNone(self.c.store.get(1))

    def test_disabled_repo_wrong_url_and_missing_test_steps_are_rejected(self):
        self.repo['enabled'] = False
        with self.assertRaises(ValueError): self.submit()
        self.repo['enabled'] = True
        with self.assertRaises(ValueError): self.submit(pr_url='https://github.com/Other/project/pull/17')
        with self.assertRaises(ValueError): self.submit(author={'summary': 'fix', 'test_steps': []})
        self.assertIsNone(self.c.store.get(1))

    def test_delivery_lock_and_pending_merge_serialize_intake(self):
        with (Path(self.c.config['state_dir']) / 'delivery.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'busy'): self.submit()
        job = self.c.store.enqueue('other', 'project', 'other', 'other')
        self.c.store.update(job['id'], state='merge_pending')
        with self.assertRaisesRegex(RuntimeError, 'serialized slot'): self.submit()
        self.qa.assert_not_called()

    def test_same_branch_cannot_be_registered_to_two_deliveries(self):
        job = self.c.store.enqueue('other', 'project', 'other', 'other')
        self.c.store.update(job['id'], state='human_review', branch='fix/workspace-change',
                            pr_url='https://github.com/ExampleOrg/project/pull/16')
        with self.assertRaisesRegex(RuntimeError, 'owned by another delivery'):
            self.submit()
        self.qa.assert_not_called()

    def test_resubmit_preserves_failed_checkout_and_records_new_head(self):
        self.qa.return_value = {'passed': False}
        with self.assertRaises(RuntimeError): self.submit()
        (self.workspace / 'feature.txt').write_text('repaired')
        git_at(self.workspace, 'add', '.'); git_at(self.workspace, 'commit', '-m', 'repair')
        self.sha = git_at(self.workspace, 'rev-parse', 'HEAD')
        self.pr['headRefOid'] = self.sha; self.head.return_value = self.sha
        self.qa.return_value = {'passed': True}
        job = self.submit()
        self.assertEqual(job['id'], 1); self.assertEqual(job['commit_sha'], self.sha)
        saved = Path(self.c.config['state_dir']) / 'jobs/1/submission-1/feature.txt'
        self.assertEqual(saved.read_text(), 'feature')
        self.assertEqual((saved.parent.parent / 'checkout/feature.txt').read_text(), 'repaired')


class SubmissionMigrationTests(unittest.TestCase):
    def test_legacy_jobs_keep_derived_branch_and_migration_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'store.sqlite'
            db = sqlite3.connect(path)
            db.execute('''CREATE TABLE jobs (id INTEGER PRIMARY KEY, external_id TEXT UNIQUE NOT NULL,
                repo TEXT NOT NULL, title TEXT NOT NULL, requirements TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', attempt INTEGER NOT NULL DEFAULT 0,
                commit_sha TEXT, pr_url TEXT, board_id TEXT, error TEXT,
                created TEXT NOT NULL, updated TEXT NOT NULL)''')
            db.execute("INSERT INTO jobs VALUES (1,'existing','project','title','requirements','human_review',0,NULL,NULL,NULL,NULL,'then','then')")
            db.commit(); db.close()
            for _ in range(2):
                store = Store(path)
                self.assertEqual(Coordinator.job_branch(store.get(1)), 'kodi/task-1')
                store.update(1, branch='fix/custom')
                self.assertEqual(Coordinator.job_branch(store.get(1)), 'fix/custom')
                store.update(1, branch=None)
                store.db.close()

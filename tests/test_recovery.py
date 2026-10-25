import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.store import Store
from kodi.recovery import retry, transient, TransientError, repair_rebase


class RetryTests(unittest.TestCase):
    def test_read_only_github_retries_but_uncertain_writes_do_not(self):
        from kodi.github import gh
        from unittest.mock import Mock
        failed=Mock(returncode=1,stderr='HTTP 503 Service Unavailable')
        passed=Mock(returncode=0,stdout='{}')
        with patch('kodi.github.environment',return_value={}), \
                patch('kodi.github.time.sleep'), \
                patch('kodi.github.subprocess.run',side_effect=[failed,passed]) as run:
            self.assertEqual(gh('api','repos/owner/repo'),'{}')
            self.assertEqual(run.call_count,2)
        with patch('kodi.github.environment',return_value={}), \
                patch('kodi.github.subprocess.run',return_value=failed) as run:
            with self.assertRaises(RuntimeError):gh('api','--method','POST','repos/owner/repo')
            self.assertEqual(run.call_count,1)
    def test_budget_survives_restart_without_spending_coding_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'queue.sqlite'; store=Store(p)
            job=store.enqueue('1','test','Test','requirements')
            with patch('kodi.recovery.time.sleep'), self.assertRaisesRegex(RuntimeError,'budget exhausted'):
                retry(store,job['id'],'author','1',lambda: (_ for _ in ()).throw(TransientError('temporary')))
            store.db.close(); store=Store(p)
            self.addCleanup(store.db.close)
            with patch('kodi.recovery.time.sleep') as sleep, self.assertRaisesRegex(RuntimeError,'budget exhausted'):
                retry(store,job['id'],'author','1',lambda: (_ for _ in ()).throw(TransientError('temporary')))
            sleep.assert_not_called()
            self.assertEqual(store.get(job['id'])['attempt'],0)

    def test_authentication_and_unknown_errors_are_not_transient(self):
        for message in ('401 token_expired','403 rate limit','refresh_token_reused',
                        'quota exceeded','billing unavailable','unknown failure'):
            self.assertFalse(transient(message))
        self.assertTrue(transient('HTTP 503 Service Unavailable'))
        self.assertTrue(transient('HTTP 429 Too Many Requests'))
        self.assertTrue(transient('API error: request failed: Post upstream: EOF'))
        self.assertFalse(transient('source syntax error: unexpected EOF'))


class RebaseTests(unittest.TestCase):
    def git(self,*args):
        return subprocess.check_output(['git','-c','user.name=Test','-c','user.email=test@example.org',
            *args],cwd=self.checkout,text=True,stderr=subprocess.DEVNULL).strip()

    def setup_conflict(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.checkout=self.root/'checkout';self.checkout.mkdir()
        self.git('init','-b','main');(self.checkout/'app.txt').write_text('initial\n')
        self.git('add','.');self.git('commit','-m','initial')
        self.git('checkout','-b','kodi/task-1');(self.checkout/'app.txt').write_text('task\n')
        self.git('commit','-am','task');self.old=self.git('rev-parse','HEAD')
        self.git('checkout','main');(self.checkout/'app.txt').write_text('upstream\n')
        self.git('commit','-am','upstream');self.base=self.git('rev-parse','HEAD')
        self.git('checkout','kodi/task-1')
        subprocess.run(['git','rebase','main'],cwd=self.checkout,capture_output=True)
        self.store=Store(self.root/'store.sqlite');self.addCleanup(self.store.db.close)
        job=self.store.enqueue('1','test','Task','Keep task and upstream');self.store.update(job['id'],commit_sha=self.old)
        self.job=self.store.get(job['id'])

    def test_resolves_in_copy_and_preserves_original(self):
        self.setup_conflict()
        def worker(config,checkout,*args,**kwargs):
            (checkout/'app.txt').write_text('upstream and task\n')
        with patch('kodi.recovery.run_worker',side_effect=worker):
            repair_rebase({},self.store,self.job,self.checkout,self.base)
        self.assertEqual((self.checkout/'app.txt').read_text(),'upstream and task\n')
        original=self.root/('rebase-recovery-'+self.old[:12]+'-1/original-checkout')
        self.assertEqual((original/'app.txt').read_text(),'task\n')
        self.assertEqual(self.git('status','--porcelain'),'')
        self.assertEqual(self.store.get(1)['attempt'],0)

    def test_rejects_out_of_scope_edits_preserving_clean_original(self):
        self.setup_conflict()
        def worker(config,checkout,*args,**kwargs):
            (checkout/'app.txt').write_text('resolved\n');(checkout/'unexpected.txt').write_text('outside scope')
        with patch('kodi.recovery.run_worker',side_effect=worker), self.assertRaisesRegex(RuntimeError,'outside'):
            repair_rebase({},self.store,self.job,self.checkout,self.base)
        self.assertEqual(self.git('rev-parse','HEAD'),self.old)
        self.assertEqual(self.git('status','--porcelain'),'')
        self.assertTrue((self.root/('rebase-recovery-'+self.old[:12]+'-1/checkout')).is_dir())

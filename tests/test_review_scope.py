import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.coordinator import Coordinator


class ReviewScopeTests(unittest.TestCase):
    def test_repairs_include_earlier_changes_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            checkout=root/'checkout'; checkout.mkdir()
            def git(*args):
                return subprocess.check_output(
                    ['git','-c','core.hooksPath=/dev/null','-c','user.name=Test',
                     '-c','user.email=test@example.invalid',*args],
                    cwd=checkout,text=True,stderr=subprocess.DEVNULL).strip()
            git('init','-b','main')
            (checkout/'baseline.txt').write_text('baseline\n')
            git('add','.'); git('commit','-m','baseline')
            git('checkout','-b','candidate')
            (checkout/'feature.py').write_text('EARLIER_FEATURE = True\n')
            git('add','.'); git('commit','-m','feature')
            (checkout/'repair.py').write_text('LATER_REPAIR = True\n')
            git('add','.'); git('commit','-m','repair')
            sha=git('rev-parse','HEAD')
            coordinator=Coordinator({'state_dir':str(root/'state'),'reviewer':'gemini'})
            try:
                job=coordinator.store.enqueue('test:scope','system','Review','Both changes')
                verdict={'approved':True,'criteria_met':True,'simple':True,
                         'blocking_findings':[],'summary':'Reviewed both changes'}
                with patch('kodi.coordinator.run_worker',return_value=(verdict,{},0)) as worker:
                    coordinator.review(job,checkout,sha,{'test_steps':['Check both']})
                prompt=worker.call_args.args[3]
                for name in ('feature.py','repair.py'):
                    self.assertIn('diff --git a/'+name,prompt)
                    self.assertIn('File '+name+':',prompt)
                self.assertNotIn('File baseline.txt:',prompt)
                evidence=coordinator.store.db.execute(
                    "SELECT sha FROM evidence WHERE job=? AND kind='review'",(job['id'],)).fetchone()
                self.assertEqual(evidence['sha'],sha)
            finally:
                coordinator.store.db.close()

    def test_refreshed_main_is_excluded_from_complete_branch_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            coordinator=Coordinator({'state_dir':str(root/'state'), 'repos': {
                'system': {'source':str(root/'source'), 'qa_suite':'system'}}})
            self.addCleanup(coordinator.store.db.close)
            job=coordinator.store.enqueue('refresh','system','Review','Candidate only')
            checkout=root/'state'/'jobs'/str(job['id'])/'checkout'
            checkout.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',
                    '-c','user.name=Test','-c','user.email=test@example.invalid',*args],
                    cwd=checkout,text=True,stderr=subprocess.DEVNULL).strip()
            def commit(path, text):
                (checkout/path).write_text(text)
                git('add','.'); git('commit','-m',text)
            git('init','-b','main')
            commit('baseline.txt','baseline')
            git('checkout','-b','kodi/task-1')
            commit('candidate.py','CANDIDATE=True')
            old=git('rev-parse','HEAD')
            coordinator.store.update(job['id'],commit_sha=old)
            coordinator.store.evidence(job['id'],old,'author',{'test_steps':['try candidate']})
            git('checkout','main')
            commit('upstream.py','UPSTREAM=True')
            subprocess.run(['git','clone',str(checkout),str(root/'source')],check=True,
                           capture_output=True)
            git('reset','--hard','HEAD~1')
            git('checkout','kodi/task-1')
            verdict={'approved':True,'criteria_met':True,'simple':True,
                     'blocking_findings':[],'summary':'candidate reviewed'}
            with patch('kodi.coordinator.qa',side_effect=lambda config, checkout, sha, suite: {'passed':True, 'sha':sha}) as qa, patch(
                    'kodi.coordinator.run_worker',return_value=(verdict,{},0)) as worker:
                refreshed=coordinator.refresh_base(job['id'])
            sha=refreshed['commit_sha']
            self.assertNotEqual(sha,old)
            self.assertTrue(coordinator.store.gate(job['id'],sha))
            qa.assert_called_once_with(coordinator.config,checkout,sha,'system')
            prompt=worker.call_args.args[3]
            self.assertIn('diff --git a/candidate.py',prompt)
            self.assertNotIn('diff --git a/upstream.py',prompt)
            self.assertNotIn('File upstream.py:',prompt)
            self.assertEqual(git('rev-parse','main'),git('rev-parse','HEAD~1'))

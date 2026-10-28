import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.coordinator import Coordinator
from kodi.review_diff import review_material


class ReviewAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.checkout=self.root/'checkout'; self.checkout.mkdir()
        self.git('init','-b','main')
        (self.checkout/'source.py').write_text('# Before context\nold = True\n# After context\n')
        (self.checkout/'image.raw').write_bytes(b'\x81\x82\x83\n')
        self.git('add','.'); self.git('commit','-m','baseline')
        self.base=self.git('rev-parse','HEAD')
        self.git('checkout','-b','candidate')

    def git(self,*args):
        return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',
            '-c','user.name=Test','-c','user.email=test@example.invalid',*args],
            cwd=self.checkout,stderr=subprocess.DEVNULL,text=True).strip()

    def candidate(self,data):
        (self.checkout/'image.raw').write_bytes(data)
        (self.checkout/'source.py').write_text(
            '# Before context\nnew = "complete UTF-8: café"\n# After context\n')
        self.git('add','.'); self.git('commit','-m','candidate')
        return self.git('rev-parse','HEAD')

    def test_raw_without_nul_and_source_complete(self):
        sha=self.candidate(b'\xff\xfe\xfd\n')
        raw=subprocess.check_output(['git','diff',self.base,sha],cwd=self.checkout)
        self.assertNotIn(b'Binary files',raw)
        with self.assertRaises(UnicodeDecodeError): raw.decode('utf-8')
        diff,context,assets=review_material(self.checkout,self.base,sha)
        self.assertIn('+new = "complete UTF-8: café"',diff)
        self.assertIn('File source.py:',context)
        self.assertEqual(assets[0]['path'],'image.raw')
        self.assertEqual(assets[0]['after']['bytes'],4)
        self.assertEqual(len(assets[0]['after']['sha256']),64)
        self.assertEqual(assets[0]['after']['git_blob'],self.git('rev-parse',sha+':image.raw'))
        source_patch=subprocess.check_output(
            ['git','diff','--no-ext-diff','--no-textconv',self.base,sha,'--','source.py'],
            cwd=self.checkout).decode('utf-8')
        self.assertIn(' # Before context\n',source_patch)
        self.assertIn('-old = True\n',source_patch)
        self.assertIn(' # After context\n',source_patch)
        coordinator=Coordinator({'state_dir':str(self.root/'state'),'reviewer':'gemini'})
        self.addCleanup(coordinator.store.db.close)
        job=coordinator.store.enqueue('complete-prompt','system','Assets','Inspect both changes')
        verdict={'approved':True,'criteria_met':True,'simple':True,
                 'blocking_findings':[],'summary':'Simulated review for prompt coverage'}
        with patch('kodi.coordinator.run_worker',return_value=(verdict,{},0)) as worker:
            coordinator.review(job,self.checkout,sha,{})
        prompt=worker.call_args.args[3]
        self.assertIn(source_patch,prompt)
        self.assertIn('File source.py:\n'+(self.checkout/'source.py').read_text(),prompt)
        self.assertIn('image.raw',prompt)
        for version in ('before','after'):
            self.assertIn(assets[0][version]['git_blob'],prompt)
            self.assertIn(assets[0][version]['sha256'],prompt)
        self.assertIn('Inspect every listed asset',prompt)

    def test_git_binary_and_deleted_asset(self):
        sha=self.candidate(b'\0binary\xff')
        diff,context,assets=review_material(self.checkout,self.base,sha)
        self.assertEqual(len(assets),1)
        self.assertIn('Asset change',diff)
        (self.checkout/'image.raw').unlink()
        self.git('add','.'); self.git('commit','-m','delete asset')
        _,_,assets=review_material(self.checkout,self.base,self.git('rev-parse','HEAD'))
        self.assertIsNone(assets[0]['after'])

    def test_utf8_and_diff_budget(self):
        self.git('rm','image.raw'); self.git('commit','-m','remove asset')
        base=self.git('rev-parse','HEAD')
        (self.checkout/'source.py').write_text('café = True\n')
        self.git('add','.'); self.git('commit','-m','utf8')
        diff,context,assets=review_material(self.checkout,base,self.git('rev-parse','HEAD'))
        self.assertFalse(assets)
        self.assertIn('+café = True',diff)
        (self.checkout/'source.py').write_text('x'*100001)
        self.git('add','.'); self.git('commit','-m','large')
        with self.assertRaisesRegex(RuntimeError,'review budget'):
            review_material(self.checkout,base,self.git('rev-parse','HEAD'))

    def test_retry_preserves_job_checkout_and_qa(self):
        sha=self.candidate(b'\xffasset')
        coordinator=Coordinator({'state_dir':str(self.root/'state'),'reviewer':'qwen'})
        self.addCleanup(coordinator.store.db.close)
        job=coordinator.store.enqueue('asset','system','Assets','Inspect both changes')
        coordinator.store.update(job['id'],state='blocked',commit_sha=sha,error='preparation failed')
        coordinator.store.evidence(job['id'],sha,'qa',{'passed':True})
        with self.assertRaisesRegex(RuntimeError,'tool-enabled reviewer'):
            coordinator.review(coordinator.store.get(job['id']),self.checkout,sha,{})
        self.assertFalse(coordinator.store.gate(job['id'],sha))
        coordinator.config['reviewer']='gemini'
        verdict={'approved':True,'criteria_met':True,'simple':True,
                 'blocking_findings':[],'summary':'Inspected assets and source'}
        with patch('kodi.coordinator.run_worker',return_value=(verdict,{},0)) as worker:
            coordinator.review(coordinator.store.get(job['id']),self.checkout,sha,{})
        self.assertIn('Inspect every listed asset',worker.call_args.args[3])
        self.assertEqual(self.git('rev-parse','HEAD'),sha)
        self.assertEqual(coordinator.store.get(job['id'])['state'],'blocked')
        rows=coordinator.store.db.execute('SELECT kind,sha FROM evidence WHERE kind!="review_context" AND job=?',(job['id'],)).fetchall()
        self.assertEqual([(r['kind'],r['sha']) for r in rows if not r['kind'].startswith(('failure:', 'delivery_attempt:'))],[('qa',sha),('review',sha)])

    def test_context_budget_and_asset_fallback_fail_closed(self):
        sha=self.candidate(b'\xffasset')
        coordinator=Coordinator({'state_dir':str(self.root/'state'),'reviewer':'gemini'})
        self.addCleanup(coordinator.store.db.close)
        job=coordinator.store.enqueue('budget','system','Budget','Review')
        with patch('kodi.coordinator.review_material',return_value=('small','x'*200001,[])), patch.object(coordinator,'review_provider') as provider:
            with self.assertRaisesRegex(RuntimeError,'context exceeds budget'):
                coordinator.review(job,self.checkout,sha,{})
            provider.assert_not_called()
        from kodi.runner import QuotaExhausted
        coordinator.config.update(reviewer='antigravity',qwen_url='https://example.invalid',
                                  qwen_model='example',qwen_env='/private/example.env')
        with patch.object(coordinator,'review_provider',side_effect=QuotaExhausted()) as provider:
            with self.assertRaisesRegex(RuntimeError,'cannot fall back'):
                coordinator.review(job,self.checkout,sha,{})
            provider.assert_called_once()
        # review() propagates errors; process() owns the blocked-state transition.
        # This assertion checks only that no successful verdict was invented.
        self.assertEqual(coordinator.store.db.execute(
            "SELECT COUNT(*) FROM evidence WHERE job=? AND kind='review'",(job['id'],)).fetchone()[0],0)

    def test_preparation_failure_blocks_process_and_retry_preserves_evidence(self):
        self.git('checkout','main')
        coordinator=Coordinator({'state_dir':str(self.root/'state'),'reviewer':'qwen',
            'repos':{'system':{'source':str(self.checkout),'qa_suite':'system'}}})
        self.addCleanup(coordinator.store.db.close)
        job=coordinator.store.enqueue('preparation','system','Assets','Inspect assets and source')
        checkout=self.root/'state'/'jobs'/str(job['id'])/'checkout'
        author={'test_steps':['Inspect both asset versions and source']}
        def write_candidate(*args,**kwargs):
            (checkout/'image.raw').write_bytes(b'\xff\xfeasset\n')
            (checkout/'source.py').write_text('new = "café"\n')
            return author,{},0
        with patch.object(coordinator,'refresh_source'), patch.object(coordinator,'move'), patch.object(
                coordinator,'progress'), patch.object(coordinator,'notification'), patch.object(
                coordinator,'publish') as publish, patch('kodi.coordinator.run_worker',
                side_effect=write_candidate) as worker, patch('kodi.coordinator.qa',
                return_value={'passed':True}) as qa:
            coordinator.process(job)
            publish.assert_not_called()
            worker.assert_called_once()  # Author only: unsupported review never launches.
        blocked=coordinator.store.get(job['id'])
        sha=blocked['commit_sha']
        self.assertEqual(blocked['state'],'blocked')
        self.assertIn('Inspect private evidence',blocked['error'])
        qa.assert_called_once_with(coordinator.config,checkout,sha,'system')
        before=[tuple(row) for row in coordinator.store.db.execute(
            'SELECT * FROM evidence WHERE kind!="review_context" AND job=? ORDER BY id',(job['id'],))]
        self.assertEqual(coordinator.store.db.execute("SELECT COUNT(*) FROM evidence WHERE kind!="review_context" AND job=? AND kind IN ('author','qa')",(job['id'],)).fetchone()[0],2)
        self.assertFalse(coordinator.store.gate(job['id'],sha))
        self.assertTrue((checkout.parent/'qa.json').is_file())
        coordinator.config['reviewer']='gemini'
        verdict={'approved':True,'criteria_met':True,'simple':True,
                 'blocking_findings':[],'summary':'Inspected both asset versions and source'}
        with patch('kodi.coordinator.run_worker',return_value=(verdict,{},0)) as reviewer:
            coordinator.review(blocked,checkout,sha,author)
        self.assertIn('+new = "café"',reviewer.call_args.args[3])
        self.assertIn('image.raw',reviewer.call_args.args[3])
        after=[tuple(row) for row in coordinator.store.db.execute(
            'SELECT * FROM evidence WHERE kind!="review_context" AND job=? ORDER BY id',(job['id'],))]
        self.assertEqual(after[:len(before)],before)
        self.assertGreater(len(after),len(before))
        self.assertEqual(dict(coordinator.store.get(job['id'])),dict(blocked))
        self.assertEqual(subprocess.check_output(['git','rev-parse','HEAD'],
            cwd=checkout,text=True).strip(),sha)
        self.assertEqual(subprocess.check_output(['git','status','--porcelain'],
            cwd=checkout),b'')

    def test_asset_review_retry_requires_exact_commit_independent_evidence(self):
        sha=self.candidate(b'\xffasset')
        coordinator=Coordinator({'state_dir':str(self.root/'state'),'reviewer':'gemini'})
        self.addCleanup(coordinator.store.db.close)
        job=coordinator.store.enqueue('gates','system','Assets','Inspect assets and source')
        coordinator.store.update(job['id'],state='blocked',commit_sha=sha,error='preparation failed')
        coordinator.store.evidence(job['id'],sha,'author',{'test_steps':['Inspect both assets']})
        coordinator.store.evidence(job['id'],self.base,'qa',{'passed':True})
        # Simulated worker responses verify gating, not independent acceptance of this patch.
        with patch('kodi.coordinator.run_worker',side_effect=RuntimeError('Cannot inspect asset')):
            with self.assertRaisesRegex(RuntimeError,'Cannot inspect asset'):
                coordinator.review(coordinator.store.get(job['id']),self.checkout,sha,{})
        self.assertEqual(coordinator.store.db.execute(
            "SELECT COUNT(*) FROM evidence WHERE job=? AND kind='review'",(job['id'],)).fetchone()[0],0)
        verdict={'approved':True,'criteria_met':True,'simple':True,
                 'blocking_findings':[],'summary':'Inspected assets and source'}
        coordinator.config['reviewer']='antigravity'  # Relevant capability change permits recovery.
        with patch('kodi.coordinator.run_worker',return_value=(verdict,{},0)):
            coordinator.review(coordinator.store.get(job['id']),self.checkout,sha,{})
        self.assertFalse(coordinator.store.gate(job['id'],sha))
        coordinator.store.evidence(job['id'],sha,'qa',{'passed':True})
        self.assertTrue(coordinator.store.gate(job['id'],sha))
        self.assertFalse(coordinator.store.gate(job['id'],self.base))
        coordinator.store.evidence(job['id'],sha,'review',dict(verdict,
            author_family='openai',reviewer_family='openai'))
        self.assertFalse(coordinator.store.gate(job['id'],sha))
        preserved=coordinator.store.get(job['id'])
        self.assertEqual(preserved['state'],'blocked')
        self.assertEqual(preserved['error'],'preparation failed')
        self.assertEqual(self.git('rev-parse','HEAD'),sha)

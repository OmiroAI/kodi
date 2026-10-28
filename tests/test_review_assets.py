import json
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

    def test_compact_assets_preserve_mode_and_exact_checkout_validation(self):
        sha=self.candidate(b'\0binary\xff')
        diff,context,assets=review_material(self.checkout,self.base,sha,mode='compact')
        self.assertEqual(context,'')
        self.assertIn('+new = "complete UTF-8: café"',diff)
        self.assertEqual(assets[0]['before']['mode'],'100644')
        self.assertEqual(assets[0]['after']['mode'],'100644')
        (self.checkout/'source.py').write_text('dirty\n')
        with self.assertRaisesRegex(RuntimeError,'clean exact'):
            review_material(self.checkout,self.base,sha,mode='compact')
        self.git('restore','source.py')
        self.git('checkout','--detach',self.base)
        with self.assertRaisesRegex(RuntimeError,'clean exact'):
            review_material(self.checkout,self.base,sha,mode='compact')

    def test_review_context_persists_in_existing_store_without_schema_change(self):
        from kodi.store import Store
        sha=self.candidate(b'\0asset')
        state=self.root/'state'
        coordinator=Coordinator({'state_dir':str(state),'reviewer':'antigravity'})
        job=coordinator.store.enqueue('context','system','Context','Inspect assets')
        verdict={'approved':True,'criteria_met':True,'simple':True,
                 'blocking_findings':[],'summary':'Synthetic verdict only'}
        with patch('kodi.coordinator.run_worker',return_value=(verdict,{},0)) as worker:
            coordinator.review(job,self.checkout,sha,{})
        # Measure the complete coordinator prompt, including worker policy.
        material=worker.call_args.args[3]
        database=coordinator.store.db.execute('PRAGMA database_list').fetchone()[2]
        coordinator.store.db.close()
        store=Store(database)
        self.addCleanup(store.db.close)
        row=store.db.execute("SELECT sha,data FROM evidence WHERE job=? AND kind='review_context'",
                             (job['id'],)).fetchone()
        self.assertEqual(row['sha'],sha)
        self.assertEqual(json.loads(row['data']),{
            'provider':'antigravity','assembled_chars':len(material),
            'assembled_utf8_bytes':len(material.encode('utf-8'))})
        self.assertGreater(len(material.encode('utf-8')),len(material))
        self.assertFalse(store.gate(job['id'],sha))

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
        self.assertEqual(coordinator.store.db.execute("SELECT COUNT(*) FROM evidence WHERE job=? AND kind IN ('author','qa')",(job['id'],)).fetchone()[0],2)
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

    def test_compact_branch_and_independent_fallback_context(self):
        self.git('rm','image.raw')
        (self.checkout/'caller.py').write_text('from source import old\n'+'# unchanged dependency\n'*150)
        self.git('add','.'); self.git('commit','-m','prepare text baseline')
        self.git('branch','-f','main','HEAD')
        base=self.git('rev-parse','HEAD')
        (self.checkout/'source.py').write_text('first_repair = True\n')
        self.git('add','.'); self.git('commit','-m','first repair')
        (self.checkout/'repair.py').write_text('second_repair = True\n')
        self.git('add','.'); self.git('commit','-m','second repair')
        sha=self.git('rev-parse','HEAD')
        # A newer upstream-only commit must never become candidate diff material.
        self.git('checkout','main')
        (self.checkout/'upstream.py').write_text('upstream_only = True\n')
        self.git('add','.'); self.git('commit','-m','upstream only')
        self.git('checkout','candidate')
        compact,context,_=review_material(self.checkout,base,sha,mode='compact')
        self.assertEqual(context,'')
        self.assertIn('+first_repair = True',compact)
        self.assertIn('+second_repair = True',compact)
        self.assertNotIn('upstream_only',compact)
        _,rich,_=review_material(self.checkout,base,sha,mode='qwen')
        self.assertIn((self.checkout/'caller.py').read_text(),rich)
        self.assertNotIn('upstream_only',rich)
        coordinator=Coordinator({'state_dir':str(self.root/'state'),
            'provider_aware_review_context':True,'qwen_url':'https://example.invalid','qwen_model':'example','qwen_env':'unused'})
        self.addCleanup(coordinator.store.db.close)
        job=coordinator.store.enqueue('compact','system','Compact','Review both repairs')
        from kodi.runner import QuotaExhausted
        with patch.object(coordinator,'review_provider',side_effect=[QuotaExhausted(),{}]) as provider:
            coordinator.review(job,self.checkout,sha,{'test_steps':['Check both repairs']})
        primary,fallback=[c.args[3] for c in provider.call_args_list]
        self.assertNotIn('File caller.py:',primary)
        self.assertIn(rich,fallback)
        self.assertIn('Check both repairs',primary)
        self.assertIn('Review both repairs',fallback)
        coordinator.config['reviewer']='qwen'
        with patch.object(coordinator,'review_provider',return_value={}) as provider:
            coordinator.review(job,self.checkout,sha,{})
        self.assertIn(rich,provider.call_args.args[3])
        coordinator.config['reviewer']='antigravity'
        coordinator.config['review_context_max_chars']=len(primary)+1
        with patch.object(coordinator,'review_provider',side_effect=QuotaExhausted()) as provider:
            with self.assertRaisesRegex(RuntimeError,'context exceeds budget'):
                coordinator.review(job,self.checkout,sha,{'test_steps':['Check both repairs']})
            provider.assert_called_once()
        (self.checkout/'caller.py').write_text('dirty\n')
        with self.assertRaisesRegex(RuntimeError,'clean exact'):
            review_material(self.checkout,base,sha,mode='compact')

    def test_toolless_context_blocks_uninspectable_dependencies_and_secrets(self):
        self.git('rm','image.raw'); self.git('commit','-m','text baseline')
        base=self.git('rev-parse','HEAD')
        (self.checkout/'dependency.bin').write_bytes(b'\0opaque')
        self.git('add','.'); self.git('commit','-m','dependency baseline')
        base=self.git('rev-parse','HEAD')
        (self.checkout/'source.py').write_text('changed = True\n')
        self.git('add','.'); self.git('commit','-m','source change')
        with self.assertRaisesRegex(RuntimeError,'binary dependencies'):
            review_material(self.checkout,base,self.git('rev-parse','HEAD'),mode='qwen')
        self.git('rm','dependency.bin')
        # Empty synthetic sentinel: no credential content is read or stored.
        (self.checkout/'.env').touch()
        self.git('add','.'); self.git('commit','-m','secret path sentinel')
        base=self.git('rev-parse','HEAD')
        (self.checkout/'source.py').write_text('changed_again = True\n')
        self.git('add','.'); self.git('commit','-m','another source change')
        with self.assertRaisesRegex(RuntimeError,'Secret-shaped path'):
            review_material(self.checkout,base,self.git('rev-parse','HEAD'),mode='qwen')

    def test_toolless_context_rejects_unchanged_external_symlink(self):
        self.git('rm','image.raw')
        (self.checkout/'dependency.py').symlink_to('/outside/source.py')
        self.git('add','.'); self.git('commit','-m','link baseline')
        base=self.git('rev-parse','HEAD')
        (self.checkout/'source.py').write_text('changed = True\n')
        self.git('add','.'); self.git('commit','-m','source repair')
        sha=self.git('rev-parse','HEAD')
        with self.assertRaisesRegex(RuntimeError,'symbolic-link dependencies'):
            review_material(self.checkout,base,sha,mode='qwen')

    def test_unknown_material_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unknown review material mode'):
            review_material(self.checkout,self.base,self.base,mode='typo')

    def test_default_fallback_remains_admissible_until_provider_aware_opt_in(self):
        self.git('rm', 'image.raw')
        (self.checkout/'unchanged.md').write_text('unchanged documentation\n' * 1200)
        self.git('add', '.')
        self.git('commit', '-m', 'large unchanged baseline')
        self.git('branch', '-f', 'main', 'HEAD')
        (self.checkout/'source.py').write_text('new = True\n')
        self.git('add', '.')
        self.git('commit', '-m', 'small runtime change')
        sha = self.git('rev-parse', 'HEAD')
        c = Coordinator({'state_dir': str(self.root/'state'),
                         'review_context_max_chars': 8000,
                         'qwen_url': 'https://example.invalid',
                         'qwen_model': 'example', 'qwen_env': 'unused'})
        self.addCleanup(c.store.db.close)
        job = c.store.enqueue('default-compatibility', 'system', 'Small change', 'Review behavior')
        from kodi.runner import QuotaExhausted
        with patch.object(c, 'review_provider', side_effect=[QuotaExhausted(), {}]) as provider:
            c.review(job, self.checkout, sha, {})
        self.assertEqual(provider.call_count, 2)
        self.assertNotIn('File unchanged.md:', provider.call_args.args[3])
        c.config['provider_aware_review_context'] = True
        with patch.object(c, 'review_provider', side_effect=QuotaExhausted()) as provider:
            with self.assertRaisesRegex(RuntimeError, 'context exceeds budget'):
                c.review(job, self.checkout, sha, {})
        provider.assert_called_once()

    def test_provider_aware_opt_in_rejects_non_boolean_configuration(self):
        for value in ('true', 1, None):
            c = Coordinator({'state_dir': str(self.root/('invalid-'+str(value))),
                             'provider_aware_review_context': value})
            self.addCleanup(c.store.db.close)
            with self.subTest(value=value), patch('kodi.coordinator.review_material') as material:
                with self.assertRaisesRegex(ValueError, 'must be a boolean'):
                    c.review({'requirements': ''}, self.checkout, 'unused', {})
                material.assert_not_called()

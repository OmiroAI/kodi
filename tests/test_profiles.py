import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.profiles import author_config, validate_profile
from kodi.runner import run_worker
from kodi.store import Store
from kodi.usage import usage_report


def configuration():
    return {'coding_profiles':{name:{'model':model,'effort':'high',
            'supported_efforts':['low','medium','high']} for name, model in
            [('sol','gpt-6.1-sol'),('terra','gpt-6.1-terra'),('luna','gpt-6-luna')]},
            'default_coding_profile':'terra','recovery_coding_profile':'sol'}


def worker_output(args):
    mount = next(arg for arg in args if arg.startswith('type=bind,src=')
                 and arg.endswith(',dst=/output'))
    return Path(mount[len('type=bind,src='):-len(',dst=/output')])


class ProfileTests(unittest.TestCase):
    def test_snapshot_survives_restart_and_config_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.sqlite'
            store=Store(path); job=store.enqueue('1','example','Task','Task')
            config=configuration()
            first=author_config(config,store,job['id'])['selected_coding_profile']
            store.db.close(); store=Store(path)
            try:
                config['coding_profiles']['terra']['model']='gpt-6-luna'
                self.assertEqual(author_config(config,store,job['id'])['selected_coding_profile'],first)
                recovery=author_config(config,store,job['id'],True)['selected_coding_profile']
                self.assertEqual(recovery['profile'],'sol')
            finally:
                store.db.close()

    def test_invalid_profiles_fail_before_dispatch(self):
        for value in ({}, {'model':'other-provider','effort':'high'},
                      {'model':'gpt-6-luna','effort':'ultra','supported_efforts':['ultra']},
                      {'model':'gpt-6.1-sol','effort':'none','supported_efforts':['high']}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_profile(value)
        store=Store(':memory:'); self.addCleanup(store.db.close)
        config=configuration(); config['recovery_coding_profile']='missing'
        with self.assertRaisesRegex(ValueError,'recovery_coding_profile'):
            author_config(config,store,1)
        self.assertEqual(store.db.execute('SELECT count(*) FROM evidence').fetchone()[0],0)
        with self.assertRaisesRegex(ValueError,'nonempty coding_profiles'):
            author_config({'coding_profiles':None},store,2)

    def test_legacy_is_readable_and_routing_requires_profiles(self):
        store=Store(':memory:'); self.addCleanup(store.db.close)
        self.assertEqual(author_config({},store,1)['selected_coding_profile']['profile'],'legacy')
        with self.assertRaisesRegex(ValueError,'Explicit coding_profiles'):
            author_config({'coding_routing_enabled':True},store,2)
        job=store.enqueue('1','example','Legacy','Task')
        store.usage(job['id'],'codex','provider default',{'input_tokens':2,'output_tokens':1},1)
        report=usage_report(store,'tasks')[str(job['id'])]
        self.assertEqual(report['providers']['codex']['total_tokens'],3)
        self.assertNotIn('author_runs',report)

    def test_explicit_command_and_provenance_for_every_phase(self):
        for profile in ('sol','terra','luna'):
            for phase in ('coding','repair','rebase_conflict_repair','rebased_candidate_repair'):
                for resolved in (None,'gpt-6.1-sol'):
                    with self.subTest(profile=profile,phase=phase,resolved=resolved), tempfile.TemporaryDirectory() as folder:
                        root=Path(folder); checkout=root/'checkout'; checkout.mkdir()
                        config=configuration(); config.update(state_dir=folder,proxy_dir=folder,worker_image='worker')
                        if profile == 'sol':
                            config['codex_harness_version']='codex-cli configured'
                        store=Store(root/'state.sqlite'); self.addCleanup(store.db.close)
                        job=store.enqueue('1','example','Task','Task')
                        config['default_coding_profile']=profile
                        config=author_config(config,store,job['id'])
                        commands=[]
                        def execute(args,**kwargs):
                            commands.append(args)
                            if 'exec' in args:
                                self.assertIn('codex --version > /output/codex-version.txt && exec "$@"',args)
                                mounted_output=worker_output(args)
                                (mounted_output/'codex-version.txt').write_text('codex-cli test\n')
                                output=mounted_output/'result.json'
                                output.write_text('{"test_steps":["Inspect"]}')
                                event={'type':'session_meta','payload':{'model':resolved}} if resolved else {'type':'thread.started'}
                                return subprocess.CompletedProcess(args,0,json.dumps(event),'')
                            return subprocess.CompletedProcess(args,0,'','')
                        with patch('kodi.runner.subprocess.run',side_effect=execute):
                            run_worker(config,checkout,root,'Task','author','codex',
                                usage_phase=phase,on_usage_event=lambda u,s,m:store.usage(job['id'],'codex','requested',u,s,**m))
                        command=next(c for c in commands if 'exec' in c)
                        self.assertEqual(sum(c[:2]==['docker','run'] for c in commands),1)
                        selection=config['selected_coding_profile']
                        self.assertEqual(command[command.index('--model')+1],selection['model'])
                        self.assertIn('model_reasoning_effort="high"',command)
                        recorded=usage_report(store,'tasks')[str(job['id'])]['author_runs'][0]
                        self.assertEqual(recorded['requested_model'],selection['model'])
                        self.assertEqual(recorded['effort'],'high')
                        self.assertEqual(recorded['profile'],profile)
                        self.assertEqual(recorded['phase'],phase)
                        self.assertEqual(recorded['resolved_model'],resolved)
                        self.assertEqual(recorded['harness_version'],'codex-cli test')
                        self.assertEqual(recorded['available_harness_version'],config.get('codex_harness_version'))
                        self.assertEqual(recorded['family'],'openai')

    def test_version_provenance_on_failure_timeout_and_unavailable_container(self):
        from kodi.runner import WorkerTimeout
        for outcome in ('failed','timeout','unavailable'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder:
                root=Path(folder); checkout=root/'checkout'; checkout.mkdir()
                output=root/'author-output'; output.mkdir()
                (output/'codex-version.txt').write_text('codex-cli stale')
                config=configuration()
                config.update(state_dir=folder,proxy_dir=folder,worker_image='worker',
                              selected_coding_profile=dict(config['coding_profiles']['terra'],profile='terra'))
                events=[]
                def execute(args,**kwargs):
                    if args[:2]==['docker','run']:
                        mounted_output=worker_output(args)
                        self.assertNotEqual(mounted_output,output)
                        self.assertFalse((mounted_output/'codex-version.txt').exists())
                        if outcome!='unavailable':
                            (mounted_output/'codex-version.txt').write_text('codex-cli actual')
                        if outcome=='timeout':
                            raise subprocess.TimeoutExpired(args,1)
                        return subprocess.CompletedProcess(args,1,'','launch failed')
                    return subprocess.CompletedProcess(args,0,'','')
                with patch('kodi.runner.subprocess.run',side_effect=execute):
                    with self.assertRaises(WorkerTimeout if outcome=='timeout' else RuntimeError):
                        run_worker(config,checkout,root,'Task','author','codex',
                                   on_usage_event=lambda data,seconds,meta:events.append((data,meta)))
                provenance=events[0][0]['author_provenance']
                self.assertEqual(provenance['harness_version'],
                                 None if outcome=='unavailable' else 'codex-cli actual')
                self.assertIsNone(provenance['resolved_model'])
                self.assertEqual(events[0][1]['outcome'],'timeout' if outcome=='timeout' else 'failed')
                self.assertEqual((output/'codex-version.txt').read_text(),'codex-cli stale')

    def test_initial_and_ordinary_repair_receive_persisted_selection(self):
        from kodi.coordinator import Coordinator
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'source'; source.mkdir(); (source/'.git').mkdir()
            config=configuration(); config.update(state_dir=str(root/'state'),max_attempts=2,
                repos={'example':{'source':str(source),'qa_suite':'example'}})
            coordinator=Coordinator(config); self.addCleanup(coordinator.store.db.close)
            job=coordinator.store.enqueue('1','example','Task','Task')
            def clone(*args,**kwargs):
                (root/'state'/'jobs'/'1'/'checkout').mkdir()
            def git(checkout,*args):
                return 'app.py' if args[:3]==('diff','--cached','--name-only') else 'candidate' if args[0]=='rev-parse' else ''
            calls=[]
            def worker(config,*args,**kwargs):
                calls.append((dict(config['selected_coding_profile']),kwargs['usage_phase']))
                coordinator.config['coding_profiles']['terra']['effort']='low'
                return {'test_steps':['Inspect']},{},0
            with patch('kodi.coordinator.subprocess.run',side_effect=clone), \
                    patch('kodi.coordinator.git',side_effect=git), \
                    patch('kodi.coordinator.run_worker',side_effect=worker), \
                    patch('kodi.coordinator.qa',return_value={'passed':False,'log':'Fix','failure_signal':'test_diagnostic'}), \
                    patch.object(coordinator,'refresh_source'), patch.object(coordinator,'move'), \
                    patch.object(coordinator,'progress'), patch.object(coordinator,'notification'):
                coordinator.process(job)
            self.assertEqual([phase for _,phase in calls],['coding','repair'])
            self.assertEqual(calls[0][0],calls[1][0])
            self.assertEqual(calls[1][0]['effort'],'high')

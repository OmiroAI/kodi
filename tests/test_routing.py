import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.routing import decision, escalate, settings
from kodi.runner import docker_args, WorkerTimeout
from kodi.store import Store


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'state.sqlite')
        self.addCleanup(self.store.db.close)
        self.job = self.store.enqueue('task', 'example', 'Edit', 'Explicit requirements')
        self.config = {'coding_routing': {'mode':'active', 'profiles':{
            name:{'model':'example-'+name.lower(), 'effort':'medium'}
            for name in ('Sol', 'Terra', 'Luna')}}}
        self.git = patch('kodi.runner.git', return_value='base').start()
        self.addCleanup(patch.stopall)

    def invoke(self, profile='Luna', **changes):
        value = dict(profile=profile, category='mechanical', reason='explicit edit',
                     uncertain=False, needs_clarification=False)
        value.update(changes)
        return Mock(return_value=(value, {}, 1))

    def route(self, invoke):
        return decision(self.config,self.store,self.job,self.root,self.root,invoke)

    def test_routes_and_restart(self):
        for name in ('Sol', 'Terra', 'Luna'):
            with self.subTest(name=name):
                self.store.db.execute('DELETE FROM evidence')
                invoke = self.invoke(name)
                result = self.route(invoke)
                self.assertEqual(result['applied_route'], name)
                self.assertEqual(self.route(invoke), result)
                self.assertEqual(invoke.call_count, 1)

    def test_invalid_uncertain_timeout_permission(self):
        for invoke in (self.invoke('Invented'), self.invoke(uncertain=True),
                       Mock(side_effect=WorkerTimeout()),
                       self.invoke(permissions=['publish'])):
            self.store.db.execute('DELETE FROM evidence')
            result = self.route(invoke)
            self.assertEqual(result['applied_route'], 'Sol')
            self.assertTrue(result['fallback_reason'])
            self.route(invoke)
            self.assertEqual(invoke.call_count, 1)

    def test_modes_and_complex_override(self):
        invoke=self.invoke()
        self.config['coding_routing']['mode']='off'
        self.assertIsNone(self.route(invoke))
        invoke.assert_not_called()
        self.config['coding_routing']['mode']='shadow'
        self.assertEqual(self.route(invoke)['applied_route'], 'Sol')
        self.config['coding_routing']['mode']='active'
        self.assertEqual(self.route(self.invoke(category='complex'))['applied_route'], 'Sol')

    def test_clarification_and_material_change(self):
        invoke=self.invoke(needs_clarification=True)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'clarification'):
                self.route(invoke)
        self.assertEqual(invoke.call_count, 1)
        self.store.update(self.job['id'], requirements='Changed requirements')
        self.job=self.store.get(self.job['id'])
        self.assertEqual(self.route(self.invoke())['applied_route'], 'Luna')

    def test_escalation_preserves_budget_and_reuses(self):
        route=self.route(self.invoke())
        self.store.update(self.job['id'], attempt=2)
        self.store.reserve_recovery(self.job['id'], 'author', '2', 2)
        result=escalate(self.config,self.store,self.job,route,'QA failure')
        self.assertEqual(result['applied_route'], 'Sol')
        self.assertEqual(escalate(self.config,self.store,self.job,result,'again'), result)
        self.assertEqual(self.route(self.invoke()), result)
        self.assertEqual(self.store.get(self.job['id'])['attempt'], 2)
        self.assertEqual(self.store.db.execute('SELECT attempts FROM recovery_attempts').fetchone()[0], 1)

    def test_readonly_mount(self):
        config=dict(proxy_dir='/example/proxy', worker_image='example-worker')
        args=docker_args(config,'triage',self.root,self.root,self.root,True)
        self.assertIn('type=bind,src='+str(self.root)+',dst=/workspace,readonly', args)

    def test_bad_operator_config(self):
        self.config['coding_routing']['profiles']['Luna']['effort']='invented'
        with self.assertRaises(ValueError):
            settings(self.config)

    def test_triage_worker_configuration_and_usage(self):
        invoke=self.invoke()
        self.route(invoke)
        args, kwargs=invoke.call_args
        self.assertEqual(args[0]['worker_timeout'], 30)
        self.assertEqual(args[0]['codex_profile'], self.config['coding_routing']['profiles']['Luna'])
        self.assertEqual(args[4:6], ('triage', 'codex'))
        self.assertEqual(kwargs['usage_phase'], 'triage')
        kwargs['on_usage_event']({'input_tokens':1}, 1, {'phase':'triage', 'outcome':'succeeded'})
        row=self.store.db.execute('SELECT * FROM usage').fetchone()
        self.assertEqual(row['phase'], 'triage')
        self.assertEqual(self.store.get(self.job['id'])['attempt'], 0)

    def test_worker_enforces_readonly_and_explicit_model(self):
        from kodi.runner import _run_worker
        config=dict(state_dir=str(self.root), proxy_dir='/example/proxy', worker_image='example-worker',
                    codex_profile={'model':'example-luna', 'effort':'low'})
        calls=[]
        def run(args, **kwargs):
            calls.append(args)
            if args[:2] == ['docker', 'run']:
                (self.root/'triage-output'/'result.json').write_text('{}')
            return Mock(stdout='', stderr='', returncode=0)
        with patch('kodi.runner.subprocess.run', side_effect=run):
            _run_worker(config,self.root,self.root,'requirements','triage','codex')
        command=calls[0]
        self.assertIn('type=bind,src='+str(self.root)+',dst=/workspace,readonly',command)
        self.assertIn('example-luna', command)
        self.assertIn('model_reasoning_effort="low"', command)

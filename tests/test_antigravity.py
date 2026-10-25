import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.policy import validate_review
from kodi.runner import run_worker

VERDICT={'approved':True,'criteria_met':True,'simple':True,'blocking_findings':[], 'summary':'Reviewed'}


class AntigravityTests(unittest.TestCase):
    def invoke(self, envelope, returncode=0, prompt='Review this diff', model='gemini-4-argon-high', raw=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); checkout=root/'checkout'; checkout.mkdir(); (checkout/'.git').mkdir()
            config={'state_dir':directory,'proxy_dir':directory,'worker_image':'worker',
                    'antigravity_model':model}
            result=subprocess.CompletedProcess([],returncode,
                stdout=raw if raw is not None else json.dumps({'event':'result','result':envelope})+'\n',stderr='')
            self.records=[]
            with patch('kodi.runner.subprocess.run',return_value=result) as execute:
                try:
                    return run_worker(config,checkout,root,prompt,'review','antigravity',
                                      lambda u,s:self.records.append(u))
                finally:
                    self.calls=execute.call_args_list

    def test_schema_verdict_usage_and_large_prompt_over_stdin(self):
        prompt='x'*200000
        value,usage,_=self.invoke({'status':'SUCCESS','structured_output':VERDICT,
                                 'usage':{'input_tokens':101,'output_tokens':20}},prompt=prompt)
        self.assertEqual(validate_review(value,'openai','google')['approved'],True)
        self.assertEqual(self.records,[usage])
        args=self.calls[0].args[0]
        self.assertNotIn(prompt,args)
        self.assertEqual(json.loads(self.calls[0].kwargs['input'])['message']['content'],prompt)
        self.assertEqual(args[args.index('--model')+1],'gemini-4-argon-high')
        self.assertNotIn('--effort',args)
        self.assertIn('--json-schema',args)
        self.assertIn('--dangerously-skip-permissions',args)
        self.assertIn('--read-only',args)
        self.assertNotIn('/var/run/docker.sock',' '.join(args))
        self.assertTrue(any('dst=/workspace,readonly' in arg for arg in args))
        self.assertEqual(args[args.index('--network')+1],'none')

    def test_non_success_status_cannot_pass_even_with_approval(self):
        for status in ['ERROR','CANCELED','WAITING','RUNNING',None]:
            with self.subTest(status=status),self.assertRaises(RuntimeError):
                self.invoke({'status':status,'structured_output':VERDICT,'usage':{'input_tokens':9}})
            self.assertEqual(self.records,[{'input_tokens':9}])

    def test_denied_tools_cannot_pass_success_payload(self):
        with self.assertRaises(RuntimeError):
            self.invoke({'status':'SUCCESS','structured_output':VERDICT,
                         'denied_actions':[{'action':'command'}],'usage':{'input_tokens':7}})
        self.assertEqual(self.records,[{'input_tokens':7}])

    def test_nonzero_exit_cannot_pass_success_payload(self):
        with self.assertRaises(RuntimeError):
            self.invoke({'status':'SUCCESS','structured_output':VERDICT},returncode=1)

    def test_free_text_approval_cannot_replace_schema_result(self):
        with self.assertRaises(RuntimeError):
            self.invoke({'status':'SUCCESS','response':json.dumps(VERDICT)})

    def test_malformed_or_missing_result_records_unknown_usage(self):
        for raw in ['not json','[]','{"event":"init"}\n','{"event":"result","result":null}\n']:
            with self.subTest(raw=raw),self.assertRaises(RuntimeError): self.invoke({},raw=raw)
            self.assertEqual(self.records,[{'usage_unknown':True}])

    def test_different_family_is_enforced_at_model_selection(self):
        for model in ['claude-opus','gpt-oss-120b',None]:
            with self.subTest(model=model),self.assertRaises(ValueError): self.invoke({},model=model)
            self.assertEqual(self.calls,[])

    def test_events_before_result_are_supported(self):
        raw=json.dumps({'event':'init'})+'\n'+json.dumps({'event':'step_update'})+'\n'+json.dumps(
            {'event':'result','result':{'status':'SUCCESS','structured_output':VERDICT}})+'\n'
        self.assertEqual(self.invoke({},raw=raw)[0],VERDICT)

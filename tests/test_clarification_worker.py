import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from kodi.clarification_worker import options,read_result,run_clarifier


class ClarificationWorkerTests(unittest.TestCase):
    def test_untrusted_result_paths_cannot_follow_or_block(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);output=root/'output';output.mkdir()
            sentinel=root/'sentinel';sentinel.write_text('unchanged')
            result=output/'result.json'
            for kind in ('symlink','fifo','directory','oversized','malformed','missing'):
                with self.subTest(kind=kind):
                    if kind=='symlink':result.symlink_to(sentinel)
                    elif kind=='fifo':os.mkfifo(result)
                    elif kind=='directory':result.mkdir()
                    elif kind=='oversized':result.write_bytes(b' '*16385)
                    elif kind=='malformed':result.write_text('bad json')
                    with self.assertRaises((OSError,ValueError)):read_result(output)
                    self.assertEqual(sentinel.read_text(),'unchanged')
                    if result.is_dir():result.rmdir()
                    else:result.unlink(missing_ok=True)
            result.write_text('{"ok":true}')
            self.assertEqual(read_result(output),{'ok':True})

    def test_worker_pins_luna_effort_readonly_checkout_and_fresh_output(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);checkout=root/'source';checkout.mkdir();(checkout/'.git').mkdir()
            config={'state_dir':str(root),'proxy_dir':str(root/'proxy'),'worker_image':'worker:fixture',
                    'clarification':{'enabled':True,'model':'gpt-6-luna','effort':'low','timeout':15}}
            invocations=[]
            def execute(args,**kwargs):
                if args[1]!='run':return subprocess.CompletedProcess(args,0)
                invocations.append(args)
                mount=next(arg for arg in args if arg.startswith('type=bind,src=') and 'dst=/output' in arg)
                output=Path(mount.split('src=',1)[1].split(',dst=',1)[0])
                # Malicious log names in the worker mount must never redirect host writes.
                (output/'events.jsonl').symlink_to(root/'sentinel')
                (output/'stderr.log').symlink_to(root/'sentinel')
                (output/'result.json').write_text('{"status":"actionable"}')
                return subprocess.CompletedProcess(args,0,'{"usage":{"input_tokens":4,"output_tokens":2}}\n','')
            (root/'sentinel').write_text('unchanged');usage=Mock()
            with patch('kodi.clarification_worker.subprocess.run',side_effect=execute):
                for _ in range(2):run_clarifier(config,checkout,root/'runs','Request',usage)
            self.assertNotEqual(invocations[0],invocations[1])
            for args in invocations:
                self.assertIn(f'type=bind,src={checkout},dst=/workspace,readonly',args)
                self.assertIn('gpt-6-luna',args)
                self.assertIn('model_reasoning_effort="low"',args)
                self.assertIn('--network',args);self.assertEqual(args[args.index('--network')+1],'none')
            self.assertEqual((root/'sentinel').read_text(),'unchanged')
            self.assertEqual(usage.call_args.args[2],{'phase':'clarification','outcome':'succeeded'})

    def test_invalid_profile_fails_before_worker(self):
        for settings in ({'enabled':True,'model':'other','effort':'low'},
                         {'enabled':True,'model':'gpt-6-luna','effort':'invented'},
                         {'enabled':True,'model':'gpt-6-luna','effort':'low','timeout':True}):
            with self.subTest(settings=settings),self.assertRaises(ValueError):options({'clarification':settings})

    def test_timeout_records_usage_and_removes_only_its_disposable_container(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);source=root/'source';source.mkdir();(source/'.git').mkdir()
            config={'state_dir':str(root),'proxy_dir':str(root/'proxy'),'worker_image':'worker:fixture',
                    'clarification':{'enabled':True,'model':'gpt-6-luna','effort':'low'}}
            usage=Mock()
            with patch('kodi.clarification_worker.subprocess.run',side_effect=[subprocess.TimeoutExpired('fixture',45),Mock()]) as run:
                with self.assertRaisesRegex(RuntimeError,'time budget'):run_clarifier(config,source,root/'runs','Request',usage)
            self.assertEqual(usage.call_args.args[2],{'phase':'clarification','outcome':'timeout'})
            self.assertEqual(run.call_args_list[1].args[0][:3],['docker','rm','-f'])


import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kodi.runner import run_worker


class FailedUsageTests(unittest.TestCase):
    def test_failed_worker_still_records_reported_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); checkout=root/'checkout'; checkout.mkdir(); (checkout/'.git').mkdir()
            config={'state_dir':folder,'proxy_dir':folder,'worker_image':'worker'}
            result=subprocess.CompletedProcess([],1,stdout=json.dumps({'type':'turn.failed','usage':{'input_tokens':123}}),stderr='failed')
            records=[]
            with patch('kodi.runner.subprocess.run',return_value=result):
                with self.assertRaises(RuntimeError):
                    run_worker(config,checkout,root,'task','author','codex',lambda u,s:records.append(u))
            self.assertEqual(records,[{'input_tokens':123}])

    def test_timeout_records_unknown_usage_and_cleans_up_container(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); checkout=root/'checkout'; checkout.mkdir(); (checkout/'.git').mkdir()
            config={'state_dir':folder,'proxy_dir':folder,'worker_image':'worker'}
            records=[]
            cleanup=subprocess.CompletedProcess([],0)
            with patch('kodi.runner.subprocess.run',side_effect=[subprocess.TimeoutExpired('worker',1),cleanup]) as execute:
                with self.assertRaises(RuntimeError):
                    run_worker(config,checkout,root,'task','author','codex',lambda u,s:records.append(u))
                self.assertEqual(execute.call_args.args[0][:3],['docker','rm','-f'])
            self.assertEqual(records,[{'usage_unknown':True,'error':'timeout'}])

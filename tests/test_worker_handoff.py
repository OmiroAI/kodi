import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from kodi.runner import RESULT_MAX_BYTES, run_worker


class WorkerHandoffTests(unittest.TestCase):
    def test_untrusted_objects_and_fresh_attempts(self):
        for role in ('author', 'review', 'triage'):
            for kind in ('valid', 'symlink', 'fifo', 'directory', 'socket',
                         'missing', 'malformed', 'oversized'):
                with self.subTest(role=role, kind=kind), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    checkout = root/'checkout'; checkout.mkdir()
                    sentinel = root/'sentinel'; sentinel.write_text('untouched')
                    legacy = root/(role+'-output'); legacy.mkdir()
                    for name in ('events.jsonl', 'stderr.log', 'result.json'):
                        (legacy/name).symlink_to(sentinel)
                    mounts = []
                    sockets = []
                    usage = Mock()

                    def worker(args, **kwargs):
                        if args[:3] == ['docker', 'rm', '-f']:
                            return subprocess.CompletedProcess(args, 0)
                        mount = next(arg for arg in args if 'dst=/output' in arg)
                        output = Path(mount.split('src=', 1)[1].split(',dst=')[0])
                        mounts.append(output)
                        self.assertFalse((output/'result.json').exists())
                        for name in ('events.jsonl', 'stderr.log'):
                            (output/name).symlink_to(sentinel)
                        result = output/'result.json'
                        if len(mounts) == 1 or kind == 'valid':
                            result.write_text('{"summary":"ok"}')
                        elif kind == 'symlink':
                            result.symlink_to(sentinel)
                        elif kind == 'fifo':
                            os.mkfifo(result)
                        elif kind == 'directory':
                            result.mkdir()
                        elif kind == 'socket':
                            sock = socket.socket(socket.AF_UNIX)
                            sock.bind(str(result)); sockets.append(sock)
                        elif kind == 'malformed':
                            result.write_text('{')
                        elif kind == 'oversized':
                            result.write_bytes(b' ' * (RESULT_MAX_BYTES + 1))
                        return subprocess.CompletedProcess(args, 0,
                            json.dumps({'usage': {'input_tokens': 7}}), 'diagnostic')

                    config = dict(state_dir=tmp, proxy_dir=tmp, worker_image='worker')
                    try:
                        with patch('kodi.runner.subprocess.run', side_effect=worker):
                            first = run_worker(config, checkout, root, '', role, 'codex')
                            self.assertEqual(first[0], {'summary': 'ok'})
                            if kind == 'valid':
                                value = run_worker(config, checkout, root, '', role, 'codex', usage)
                                self.assertEqual(value[0], first[0])
                            else:
                                with self.assertRaises((RuntimeError, ValueError)):
                                    run_worker(config, checkout, root, '', role, 'codex', usage)
                        self.assertNotEqual(*mounts)
                        self.assertEqual(sentinel.read_text(), 'untouched')
                        usage.assert_called_once()
                        evidence = list((root/(role+'-evidence')).glob('attempt-*'))
                        self.assertEqual(len(evidence), 2)
                        for attempt in evidence:
                            self.assertEqual((attempt/'stderr.log').read_text(), 'diagnostic')
                            self.assertTrue((attempt/'events.jsonl').is_file())
                            self.assertNotIn(attempt, mounts)
                        self.assertEqual(sum((p/'result.json').exists() for p in evidence),
                                         2 if kind == 'valid' else 1)
                    finally:
                        for sock in sockets:
                            sock.close()

    def test_reviewer_stream_logs_are_host_only(self):
        for provider in ('gemini', 'antigravity'):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                checkout = root/'checkout'; checkout.mkdir()
                sentinel = root/'sentinel'; sentinel.write_text('untouched')
                verdict = dict(approved=True, criteria_met=True, simple=True,
                               summary='ok', blocking_findings=[])

                def worker(args, **kwargs):
                    if args[:3] == ['docker', 'rm', '-f']:
                        return subprocess.CompletedProcess(args, 0)
                    mount = next(arg for arg in args if 'dst=/output' in arg)
                    output = Path(mount.split('src=', 1)[1].split(',dst=')[0])
                    for name in ('events.jsonl', 'stderr.log', 'result.json'):
                        (output/name).symlink_to(sentinel)
                    if provider == 'gemini':
                        data = dict(response=json.dumps(verdict), stats={'tokens': 3})
                    else:
                        data = dict(event='result', result=dict(status='SUCCESS',
                                    structured_output=verdict, usage={'tokens': 3}))
                    return subprocess.CompletedProcess(args, 0, json.dumps(data), 'diagnostic')

                with patch('kodi.runner.subprocess.run', side_effect=worker):
                    value, _, _ = run_worker(dict(state_dir=tmp, proxy_dir=tmp,
                        worker_image='worker'), checkout, root, '', 'review', provider)
                self.assertEqual(value, verdict)
                self.assertEqual(sentinel.read_text(), 'untouched')
                self.assertEqual(len(list((root/'review-evidence').glob('attempt-*/stderr.log'))), 1)
